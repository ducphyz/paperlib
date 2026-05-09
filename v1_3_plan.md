# paperlib v1.3 — Full-Text Search Implementation Plan

Rough plan for the v1.3 milestone. Scope: PDF→Markdown extraction, full-text
chunked index, and multi-method search (keyword, fuzzy, semantic, hybrid).

---

## Invariants

- JSON records remain source of truth. All search artifacts are rebuildable.
- `text/` directory (singular) is the canonical location. No `texts/`.
- SQLite `db/library.db` is extended with new tables (migration v3).
- Embeddings stored in SQLite as BLOBs. No separate `.npy` files.
- Existing `.txt` files are preserved for compatibility and debugging.
- **For v1.3 search indexing, validated Markdown is the preferred full-text
  source.** If valid Markdown exists, chunk Markdown. If missing or invalid,
  fall back to `.txt` with lower location confidence.
- All new write paths respect `--dry-run`.
- Locked fields are never overwritten.

---

## New source layout

```
src/paperlib/
  pipeline/
    extract_md.py                # orchestrator: picks provider, validates, writes file
    markdown_extractors/
      __init__.py
      base.py                    # MarkdownExtractor Protocol + MarkdownExtractionResult
      openai_pdf.py              # first implementation (uses existing ai/client.py)
      validate.py                # validation logic
  search/
    __init__.py
    models.py                    # SearchResult, Chunk, ScoreBreakdown dataclasses
    normalize.py                 # text/query normalization
    aliases.py                   # domain alias expansion (reads config/search_aliases.toml)
    chunking.py                  # Markdown/txt → Chunk list with section/page metadata
    index.py                     # rebuild search database (orchestrator)
    fts.py                       # SQLite FTS5 keyword search
    fuzzy.py                     # RapidFuzz over metadata fields
    embeddings.py                # embed text, store/load vectors from SQLite
    ranking.py                   # combine signals → ranked results
    service.py                   # high-level search(query, mode) → SearchResult[]

config/
  search_aliases.toml            # physics domain alias file (committed to repo)
```

New runtime directory: none. New DB tables in existing `db/library.db`.

---

## Phase 1 — Extraction result model and MarkdownInfo in FileRecord

**Goal:** establish the data structures before writing any extraction code.

### `MarkdownExtractionResult` dataclass

Lives in `pipeline/markdown_extractors/base.py`. Mirrors the existing
`ExtractionInfo` pattern in [`models/file.py`](src/paperlib/models/file.py).

```python
@dataclass
class MarkdownExtractionResult:
    success: bool
    markdown: str | None
    provider: str            # "openai_pdf" | "claude_pdf" | ...
    model: str
    source: str              # "api_pdf"
    validation_status: str   # "unvalidated" on return from provider; filled by orchestrator
    validation_errors: list[str]
    page_count: int | None
    created_at: str
```

### `MarkdownInfo` dataclass in `FileRecord`

Add to [`models/file.py`](src/paperlib/models/file.py) alongside the existing
`ExtractionInfo`. Attach as `markdown: MarkdownInfo | None = None` to
`FileRecord` — nullable so all existing records are unaffected.

```python
@dataclass
class MarkdownInfo:
    markdown_path: str | None   # None when status is "failed" (no file written)
    provider: str               # "openai_pdf" | "claude_pdf" | ...
    model: str
    status: str                 # "validated" | "partial" | "failed"
    validation_errors: list[str]
    page_count: int | None
    created_at: str
```

`MarkdownInfo` serialises via `to_dict()` / `from_dict()` exactly like
`ExtractionInfo`.

### `MarkdownExtractor` Protocol

```python
class MarkdownExtractor(Protocol):
    def extract(self, pdf_path: Path, config: Config) -> MarkdownExtractionResult:
        ...
```

`page_count` in `MarkdownExtractionResult`: the provider may return `None`.
The orchestrator computes `page_count` locally from the PDF (e.g. via
`pdfplumber`) and overwrites `result.page_count` before validation and
persistence. API-reported page counts must not be trusted.

---

## Phase 2 — Direct PDF → API Markdown extractor

**Goal:** produce `text/<paper_id>.md` from the original PDF via the AI API.

### Phase 2A — Extend `ai/client.py` with PDF/file input

`ai/client.py` currently wraps text-only chat completion. Before writing the
extractor, add a provider-neutral method:

```python
def extract_markdown_from_pdf(
    self, pdf_path: Path, prompt: str, model: str
) -> str:  # raw Markdown string
```

All provider-specific details (base64 encoding, multipart upload, file object
API) live inside `ai/client.py`, not inside the extractor. `openai_pdf.py`
calls this method and knows nothing about the wire format.

### Phase 2B — Provider implementation

`pipeline/markdown_extractors/openai_pdf.py` — first and only implementation
for v1.3. Calls `ai/client.py` via the method added in Phase 2A.

**Responsibility boundary:** providers **extract only**; they do not validate.
The provider returns `MarkdownExtractionResult` with `validation_status =
"unvalidated"` and `validation_errors = []`. The orchestrator (Phase 2 below)
calls `validate_markdown()` and sets those fields before writing any files.
This prevents validation logic from being duplicated inside provider code.

- Send the PDF file (base64 or multipart upload depending on API) with a
  structured prompt requesting clean Markdown.
- Prompt specifies: page markers (`<!-- page: N -->`), section headings (`##`),
  abstract, figure captions, table captions, equations (best-effort), and
  references section.
- Returns `MarkdownExtractionResult` (validation fields unset).

### Orchestrator

`pipeline/extract_md.py` — `extract_markdown(paper_id, config, force=False)`

1. Resolve PDF path from record.
2. Skip if `file_record.markdown` already set and `force=False`.
3. Pick provider from config.
4. Call provider `.extract()`.
5. Run validation (Phase 3).
6. If validated: write `.md` file atomically to `text/<paper_id>.md` (stable,
   ID-based name; not tied to PDF filename), update `FileRecord.markdown`.
7. Write updated JSON record atomically.
8. Return `MarkdownExtractionResult`.

### CLI

```
paperlib extract-markdown <id>          # single paper
paperlib extract-markdown --all         # all papers missing validated markdown
paperlib extract-markdown --failed      # retry previously failed extractions
paperlib extract-markdown --force       # re-extract even if already exists
```

A **failed** extraction writes `MarkdownInfo(status="failed", markdown_path=None,
validation_errors=[...])` into `FileRecord`. `--failed` selects records where
`file_record.markdown` is not None and `file_record.markdown.status == "failed"`.
This requires persisting failure metadata into the JSON record — not just logs.

Ingest does **not** call markdown extraction automatically. Separate step.
Later versions may add `paperlib ingest --extract-markdown` as opt-in.

---

## Phase 3 — Markdown validation

**Goal:** reject malformed API output before it enters the search index.

Lives in `pipeline/markdown_extractors/validate.py`.

`validate_markdown(text, page_count) -> tuple[str, list[str]]`
Returns `(status, errors)` where status is `"validated"`, `"partial"`, or
`"failed"`.

### Required checks

- Output is non-empty.
- Output does not contain API refusal or error boilerplate. Check only the
  first ~1000 characters for phrases like "I cannot", "As an AI", "I'm sorry"
  — checking the full document risks false-positives from quoted paper text.
- Output length heuristic: < 50 words/page on average is suspicious and
  contributes to `"partial"` status. This is a soft signal, not a hard fail —
  figure-heavy papers, short letters, and equation-dense PDFs legitimately
  produce less text per page. Extremely short output (< 10 words total) → fail.
- At least one `##` heading is present.
- At least one page marker is present.
- Page markers follow exactly the canonical format: `<!-- page: N -->`.
  No alternatives (`Page 1`, `[Page 1]`, etc.) are accepted.
- Page marker numbers are monotonically increasing.
- First page marker is `<!-- page: 1 -->`.
- For PDFs with ≥ 10 pages: a references or bibliography section is expected
  (check for `## References`, `## Bibliography`, case-insensitive).

### Status rules

- All checks pass → `"validated"`
- Page markers present but non-monotonic, or references missing for long
  papers, or suspiciously short output per page → `"partial"` (usable for
  chunking if `markdown_fallback_partial = true` in config)
- No page markers, or refusal detected, or empty/near-empty output → `"failed"`
  (not indexed; `.txt` fallback used)

---

## Phase 4 — Search field augmentation *(optional — not on critical path)*

**Goal:** add missing structured fields to `summary.physics` for richer indexed
search coverage. This phase is **not required** for v1.3 search to function.
Defer or skip if it slips the milestone; FTS and semantic search work without
the new fields.

### New fields in `summary.physics`

```json
{
  "phenomena": [],   // e.g. "induced superconductivity", "spin-orbit coupling"
  "quantities": [],  // e.g. "quality factor", "superfluid density"
  "aliases": []      // AI- or user-assigned abbreviations, e.g. ["CPW"]
}
```

Existing fields (`materials`, `devices`, `measurements`, `main_theory`) are
unchanged.

### Implementation

- Update `PaperRecord` model and JSON schema.
- Update AI summarisation prompt in `ai/prompts.py` to elicit these fields.
- `re-summarise --search-fields`: backfill only the new fields on existing
  records without regenerating the full summary.
- Add `search_text_version: str` to `summary` dict (e.g. `"v1.3"`) for
  invalidation tracking.

---

## Phase 5 — SQLite migration v3

**Goal:** add chunk, FTS, and embedding tables to `db/library.db`.

Migration registered in `store/migrations.py` as `MIGRATIONS[3]`.

### New tables

```sql
-- Markdown chunks
-- Integer PK required for FTS5 external content table content_rowid
CREATE TABLE chunks (
    id                  INTEGER PRIMARY KEY,
    chunk_id            TEXT UNIQUE NOT NULL,   -- "{paper_id}_c{04d}"
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    file_hash           TEXT NOT NULL,
    source_type         TEXT NOT NULL,          -- "markdown_api" | "local_txt"
    location_confidence TEXT NOT NULL,          -- "high" | "medium" | "low"
    section_title       TEXT,
    page_start          INTEGER,
    page_end            INTEGER,
    chunk_order         INTEGER NOT NULL,
    text                TEXT NOT NULL,
    text_hash           TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

-- FTS5 over paper metadata (standalone, not external-content).
-- Populated explicitly by rebuild-search-index from JSON records.
-- Avoids schema coupling to the `papers` table — no risk from missing columns,
-- no FTS corruption if papers rows are modified directly.
CREATE VIRTUAL TABLE paper_fts USING fts5 (
    paper_id UNINDEXED,
    title,
    authors,
    year,
    tags,
    summary_short,
    summary_technical,
    materials,
    devices,
    phenomena,
    quantities,
    aliases,
    methods,
    key_contributions
);

-- FTS5 over chunk text (external content, integer id)
CREATE VIRTUAL TABLE chunk_fts USING fts5 (
    chunk_id UNINDEXED,
    paper_id UNINDEXED,
    section_title,
    text,
    content='chunks',
    content_rowid='id'
);

-- Paper-level embeddings
CREATE TABLE paper_embeddings (
    paper_id     TEXT PRIMARY KEY REFERENCES papers(paper_id),
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,  -- vector length; detect shape mismatch on load
    vector       BLOB NOT NULL,     -- float32 numpy array, tobytes()
    source_hash  TEXT NOT NULL,     -- sha256 of source text; skip re-embed if unchanged
    created_at   TEXT NOT NULL
);

-- Chunk-level embeddings
CREATE TABLE chunk_embeddings (
    chunk_id     TEXT PRIMARY KEY REFERENCES chunks(chunk_id),
    paper_id     TEXT NOT NULL,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,  -- vector length; detect shape mismatch on load
    vector       BLOB NOT NULL,
    source_hash  TEXT NOT NULL,     -- sha256 of chunk text; skip re-embed if unchanged
    created_at   TEXT NOT NULL
);
```

---

## Phase 6 — Chunking

**Goal:** split full text into overlapping chunks carrying source quality
metadata.

`search/chunking.py` — `chunk_document(record, config) -> list[Chunk]`

### Source priority

1. `file_record.markdown` status is `"validated"` → chunk from `.md`
   - `source_type = "markdown_api"`, `location_confidence = "high"`
2. `file_record.markdown` status is `"partial"` and config allows partial →
   chunk from `.md` with a warning
   - `source_type = "markdown_api"`, `location_confidence = "medium"`
3. No Markdown or failed → chunk from `.txt`
   - `source_type = "local_txt"`, `location_confidence = "low"`
   - `page_start` and `page_end` are `null`

### Chunk ID stability

`chunk_id = "{paper_id}_c{N:04d}"` is deterministic from `paper_id` and
`chunk_order`. For unchanged source text and chunking config, a rebuild must
produce identical `chunk_id` values — this is what allows existing embeddings
to survive a rebuild when `source_hash` matches. If chunking config changes
(size, overlap), `text_hash`/`source_hash` changes and embeddings are
recomputed on the next `embed` or `rebuild-search-index --embeddings` run.

### Chunking rules

- Target chunk size: 250 words. Overlap: 60 words. This is a pragmatic size
  for `all-MiniLM-L6-v2`; technical prose may tokenize to more than 256
  wordpieces, in which case the model silently truncates the tail.
- Split on `##` section boundaries first, then by word count within sections.
- Page markers `<!-- page: N -->` used to assign `page_start` / `page_end` to
  each chunk. Removed from stored chunk text.
- Each `Chunk` carries: `chunk_id`, `paper_id`, `file_hash`, `source_type`,
  `location_confidence`, `section_title`, `page_start`, `page_end`,
  `chunk_order`, `text`, `text_hash`.

---

## Phase 7 — Rebuild search index

**Goal:** single command that (re)builds chunks, FTS, and optionally embeddings.

```
paperlib rebuild-search-index               # chunks + FTS only
paperlib rebuild-search-index --embeddings  # + embeddings
paperlib rebuild-search-index --force       # clear and rebuild all
```

**`--force` semantics:** clears and rebuilds `chunks`, `paper_fts`, and
`chunk_fts`. Does **not** delete `paper_embeddings` or `chunk_embeddings`
unless `--embeddings` is also passed. Without `--embeddings`, existing
embeddings whose `source_hash` still matches are preserved; orphaned
`chunk_embeddings` whose `chunk_id` no longer exists in `chunks` are deleted.

`search/index.py` orchestrates:

1. For each paper in records, determine full-text source (Phase 6 priority).
2. Chunk and write/update `chunks` table.
3. Repopulate `paper_fts`: delete all rows, then reinsert from JSON records.
4. Repopulate `chunk_fts` using the FTS5 rebuild command after replacing chunk
   rows. The chunk table replacement and FTS rebuild must happen inside a single
   transaction to prevent a partially-updated search index:
   ```sql
   -- inside one transaction
   DELETE FROM chunks WHERE paper_id = ?;
   INSERT INTO chunks ...;
   INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild');
   ```
   This requires that `chunks.id` (the `content_rowid`) is always in sync.
5. Populate `paper_fts` by reading JSON record fields directly (title, authors,
   tags, summary, physics structured fields) — not from the `papers` SQL table.
6. If `--embeddings`: call the same embedding logic used by `paperlib embed`
   for each paper and chunk that lacks a current embedding. Delete orphaned
   `chunk_embeddings` for chunk_ids no longer in `chunks`.

`paperlib rebuild-search-index --embeddings` and `paperlib embed` use the same
underlying code. The difference: `rebuild-search-index` always rebuilds chunks
and FTS first; `paperlib embed` skips chunk/FTS and updates embeddings only.
Running both is safe but redundant — use `rebuild-search-index --embeddings`
when rebuilding from scratch, `paperlib embed` for incremental updates.

Progress reported as plain `print` lines. No external progress bar dep.

---

## Phase 8 — Normalization and aliases

**Goal:** deterministic query expansion handling hyphen variants,
abbreviations, and physics-domain synonyms.

### `search/normalize.py`

- Lowercase
- Unicode dash normalization → ASCII hyphen
- Collapse whitespace
- Strip trailing punctuation
- Simple plural/singular (heuristic suffix removal, not full lemmatization)

### `search/aliases.py`

Reads `config/search_aliases.toml`. Maps abbreviation or variant → list of
canonical forms. Applied to queries at search time — not stored in the index.

```toml
[aliases]
cpw       = ["coplanar waveguide", "coplanar waveguide resonator", "CPW resonator"]
soc       = ["spin-orbit coupling", "spin orbit coupling", "spin-orbit interaction"]
alinas    = ["Al-InAs", "Al/InAs", "AlInAs"]
sf_hybrid = ["superconductor-ferromagnet", "S/F hybrid", "superconductor ferromagnet bilayer"]
jj        = ["Josephson junction", "tunnel junction"]
vna       = ["vector network analyzer", "VNA"]
2deg      = ["two-dimensional electron gas", "2DEG"]
qd        = ["quantum dot"]
sc        = ["superconductor", "superconducting"]
```

Expanded terms are included in `--json` output so queries are transparent.

---

## Phase 9 — Embeddings

**Goal:** precompute and store dense vectors for semantic search.

### Backend

v1.3 uses only the local `sentence-transformers` backend. OpenAI, Qwen, and
OpenAI-compatible (OpenRouter, Ollama, etc.) embedding backends are deferred to
a future version. The schema retains `model`, `dimension`, and `source_hash`
fields so future backends can slot in without a migration.

```toml
[search]
embedding_backend = "local"
embedding_model   = "sentence-transformers/all-MiniLM-L6-v2"
```

Use `SentenceTransformer.encode(..., normalize_embeddings=True)`. Store vectors
as `float32` SQLite BLOBs via `ndarray.tobytes()`. Expected dimension: 384.
Similarity is dot product (equivalent to cosine when vectors are normalized).

First run downloads the model from Hugging Face (~90 MB). After the cache
exists, `embed` and `search` run fully offline.

If `sentence-transformers` is not installed, exit with:
```
Error: sentence-transformers is not installed. Install with:
  pip install -e ".[search]"
```

### Paper embedding source text

Concatenation of: `title | authors | summary.short | summary.technical | tags |
materials | devices | phenomena | quantities | aliases | methods`

### Chunk embedding source text

`{section_title}: {chunk_text}` (section title prefix improves chunk retrieval).

### Incremental update

Skip embed if `source_hash` (sha256 of source text) matches stored value.

### CLI

```
paperlib embed                   # embed all missing
paperlib embed --force           # re-embed all
paperlib embed --backend local   # override config
```

`paperlib embed` requires chunks to exist in the database. If the `chunks`
table is empty, exit with:
```
Error: no chunks found. Run `paperlib rebuild-search-index` first.
```

---

## Phase 10 — Keyword search (FTS5)

`search/fts.py` — `keyword_search(query, db, n=20) -> list[FtsHit]`

- Normalize query, expand aliases.
- Build the FTS5 query string through a safe query builder. Physics queries
  contain syntax-sensitive characters (`-`, `/`, `±`, `:`, `(`) that break
  raw FTS5 parsing — e.g. `p-wave`, `Al/InAs`, `p±ip`. Escape or quote terms
  to prevent parse errors, but do not force every multi-word expansion into a
  single exact phrase — that reduces recall. Prefer token-level queries with
  boolean operators; reserve phrase quoting for terms where word order matters.
- Search `paper_fts` → paper-level hits with rank and matched fields.
- Search `chunk_fts` → chunk hits; group by paper, keep top 3 snippets.
- FTS5 `snippet()` function for excerpt generation.
- Snippets from `.txt`-sourced chunks labeled with `location_confidence: "low"`.
- Return `FtsHit(paper_id, paper_rank, chunk_rank, matched_fields, snippets)`.

---

## Phase 11 — Fuzzy search

`search/fuzzy.py` — `fuzzy_search(query, records, threshold=85) -> list[FuzzyHit]`

- RapidFuzz over: title, author names, tags, materials, devices, aliases.
- Does **not** run over chunk text (prohibitively slow).
- Returns `FuzzyHit(paper_id, field, matched_value, score)`.
- Labeled as fuzzy in all output so it does not dominate exact matches.

---

## Phase 12 — Semantic search

`search/embeddings.py` — `semantic_search(query, db, n=20) -> list[SemanticHit]`

1. Normalize + expand query.
2. Embed expanded query text.
3. Load `paper_embeddings`, skipping any row whose `source_hash` does not
   match the current source text hash for that paper. Stale vectors must not
   be used — they survive rebuilds where chunks changed but `--embeddings` was
   not passed.
4. Cosine similarity vs. loaded paper vectors → top-K paper scores.
5. Load `chunk_embeddings`, skipping any row whose `source_hash` does not
   match the current `chunks.text_hash`.
6. Cosine similarity vs. loaded chunk vectors → top-3 chunks per paper.
7. Return `SemanticHit(paper_id, paper_score, chunk_hits)`.

Requires embeddings to exist. If none are found when `--mode semantic` is
requested, print a clear actionable message and exit non-zero:

```
Error: no embeddings found. Run `paperlib embed` first, or use --mode keyword.
```

In hybrid mode only, missing embeddings degrade gracefully — semantic
component is skipped and a single warning line is printed; keyword and fuzzy
results are still returned. Chunks sourced from `.txt` are returned with
`location_confidence: "low"` and no `page_start`/`page_end`.

---

## Phase 13 — Hybrid ranking

`search/ranking.py` — `rank(fts_hits, fuzzy_hits, semantic_hits) -> list[SearchResult]`

### Score formula

```
final = (
    0.20 * paper_fts_score
  + 0.20 * chunk_fts_score
  + 0.25 * paper_semantic
  + 0.20 * chunk_semantic
  + 0.10 * structured_field_match   # exact hit in materials/devices/etc.
  + 0.03 * alias_match
  + 0.02 * fuzzy_match
)
```

All component scores normalized 0–1 before combining.

### Multi-concept bonus

Strip common English stop words from the normalized query to produce a set of
meaningful terms (e.g. "resonator", "ferromagnet" from "weird cpw ferromagnet
resonator"). Papers whose indexed fields match ≥ 2 distinct terms from this set
get +0.10 additive bonus. Cap final score at 1.0. No noun-phrase extraction —
keep it simple and deterministic.

### Why-matched explanations

One plain-English string per match reason:
- `"matched devices: CPW resonator"`
- `"matched chunk in section 'Device Design' (page 3-4)"`
- `"fuzzy match: resnator → resonator"`
- `"alias expansion: cpw → coplanar waveguide resonator"`
- `"chunk location approximate (txt source)"`

---

## Phase 14 — Extended `search` command and JSON output

Extend existing `paperlib search` rather than adding a new command.

```
paperlib search "query"                  # hybrid (default)
paperlib search "query" --mode keyword
paperlib search "query" --mode fuzzy
paperlib search "query" --mode semantic
paperlib search "query" --mode hybrid
paperlib search "query" --top 10
paperlib search "query" --json
```

### Terminal output (default)

```
Results for: "weird cpw waveguide resonator"
Expanded: cpw → coplanar waveguide resonator

  #  handle_id          year  score  why
  1  smith_2022         2022  0.91   matched devices: CPW resonator; chunk in §Device Design (p3-4)
  2  jones_2021         2021  0.78   alias: cpw → coplanar waveguide; matched materials: Al-InAs
  3  chen_2020          2020  0.65   semantic match; fuzzy: resnator → resonator [txt source]
```

### JSON output (`--json`)

```json
{
  "query": "weird cpw waveguide resonator",
  "normalized_query": "weird cpw waveguide resonator",
  "expanded_terms": ["cpw", "coplanar waveguide", "coplanar waveguide resonator"],
  "mode": "hybrid",
  "results": [
    {
      "paper_id": "p_xxx",
      "handle_id": "smith_2022",
      "title": "...",
      "year": 2022,
      "score": 0.91,
      "score_breakdown": {
        "paper_fts": 0.7,
        "chunk_fts": 0.8,
        "paper_semantic": 0.9,
        "chunk_semantic": 0.85,
        "structured_field": 0.6,
        "alias": 0.5,
        "fuzzy": 0.0
      },
      "why": [
        "matched devices: CPW resonator",
        "matched chunk in section 'Device Design' (page 3-4)"
      ],
      "relevant_chunks": [
        {
          "chunk_id": "p_xxx_c004",
          "section_title": "Device Design",
          "source_type": "markdown_api",
          "location_confidence": "high",
          "page_start": 3,
          "page_end": 4,
          "score": 0.82,
          "snippet": "..."
        }
      ]
    }
  ]
}
```

JSON schema is stable — no rich terminal escapes included.

---

## Phase 15 — Config additions

All new config keys go in `config.toml` and `config.example.toml`. Flat keys
under existing sections to match current style.

```toml
[extraction]
# existing keys unchanged
engine            = "pdfplumber"
markdown_backend  = "none"        # "openai_pdf" | "none"; default "none" only to
                                  # avoid accidental API cost. Preferred v1.3 path
                                  # is "openai_pdf". Set explicitly to enable.
markdown_model    = "gpt-5.4"     # OpenAI model with PDF/file input support
markdown_require_validation    = true
markdown_fallback_to_txt       = true
markdown_fallback_partial      = false   # use "partial" validated markdown

[search]
embedding_backend  = "local"
embedding_model    = "sentence-transformers/all-MiniLM-L6-v2"
alias_file         = ""                  # blank → uses config/search_aliases.toml
default_mode       = "hybrid"            # "keyword" | "fuzzy" | "semantic" | "hybrid"
top_n              = 10
```

---

## Phase 16 — Tests

### Fixtures

5–8 synthetic records in `tests/fixtures/` with matching `.md` and `.json`
files covering: superconductor-ferromagnet hybrid, CPW resonator, spin-orbit
coupling, AlInAs, quantum dot transport. Controlled text for predictable hits.

Fixture `.md` files use canonical `<!-- page: N -->` markers and `##` headings
so chunking tests are deterministic.

### Test coverage

- `test_validate_markdown.py` — all validation checks, refusal detection,
  monotonic markers, partial vs. failed classification
- `test_chunking.py` — chunk count, section headers, page markers,
  `source_type` / `location_confidence` for Markdown vs. txt source
- `test_normalize.py` — hyphen variants, alias expansion, round-trip
- `test_fts.py` — exact title match, author match, chunk hit, no-result case
- `test_fuzzy.py` — typo cases (resnator, feromagnetic, bogolubov)
- `test_semantic.py` — mock embeddings (cosine sim math only, no model load)
- `test_ranking.py` — weight formula, multi-concept bonus, score cap
- `test_index_rebuild.py` — idempotency (run twice, same chunk and FTS state)
- `test_search_json.py` — JSON schema validation against known fixture query
- `test_search_degraded.py` — hybrid search with no embeddings emits warning
  line and returns keyword + fuzzy results; semantic mode exits non-zero

---

## Dependencies

New runtime deps:

| Package | Required? | Purpose |
|---|---|---|
| `rapidfuzz` | **Required** | Fuzzy string matching (core search feature) |
| `numpy` | **Required** | Vector math for embeddings |
| `sentence-transformers` | Optional | Local embedding model (~80 MB download) |

`rapidfuzz` and `numpy` are small and always required — fuzzy search is a
core feature, not an optional extra. Only `sentence-transformers` is optional
(needed only when `embedding_backend = "local"`).

```toml
# pyproject.toml
dependencies = [
    "rapidfuzz",
    "numpy",
    ...
]

[project.optional-dependencies]
search = ["sentence-transformers"]
```

Install:
- Core (keyword + fuzzy): no extra step — `rapidfuzz` and `numpy` are bundled.
- With local embeddings: `pip install -e ".[search]"` (dev) or
  `pip install paperlib[search]` (published).

---

## Migration notes (for CHANGELOG)

After upgrading to v1.3:

1. `rapidfuzz` and `numpy` are bundled as required deps — no extra install step
   for keyword and fuzzy search. For local embeddings only:
   - Published package: `pip install paperlib[search]`
   - Local development: `pip install -e ".[search]"`
2. Optional: `paperlib extract-markdown --all` — produces `.md` files for all
   papers. Requires AI API key; costs API calls per PDF. **Run this before
   rebuilding the index** so the chunker uses high-confidence Markdown. Without
   this, chunking falls back to `.txt` with low location confidence.
3. `paperlib rebuild-search-index` — builds chunks and FTS tables. Fast; no
   embeddings. Keyword and fuzzy search are usable after this step.
4. Optional: `paperlib embed` — computes embeddings (downloads model on first
   run for local backend). Required for semantic and hybrid search.

Recommended workflow after upgrade:
```
paperlib extract-markdown --all
paperlib rebuild-search-index --embeddings
paperlib search "ferromagnetic hybrid resonator"
```

---

## Definition of done (v1.3)

**Extraction**
- [ ] `ai/client.py` extended with `extract_markdown_from_pdf()` (Phase 2A)
- [ ] `MarkdownExtractionResult` and `MarkdownInfo` dataclasses implemented
- [ ] `MarkdownExtractor` Protocol defined; `openai_pdf` provider implemented
      via `extract_markdown_from_pdf()` — no wire-format details in the extractor
- [ ] Orchestrator computes `page_count` locally from PDF; overwrites
      provider-returned value before validation
- [ ] `paperlib extract-markdown` produces validated `.md` files at
      `text/<paper_id>.md`
- [ ] Markdown validation rejects malformed outputs (refusals scanned in first
      ~1000 chars only, missing markers, non-monotonic pages)
- [ ] Suspiciously short output → `"partial"`; empty/near-empty → `"failed"`
- [ ] Failed extraction writes `MarkdownInfo(status="failed", markdown_path=None)`
      into `FileRecord` and persists to JSON
- [ ] `extract-markdown --failed` selects records where
      `file_record.markdown.status == "failed"`
- [ ] Re-running `extract-markdown` is idempotent unless `--force` is used

**Indexing**
- [ ] `paperlib rebuild-search-index` builds schema v3, chunks, FTS
- [ ] Chunk table replacement and `chunk_fts` rebuild happen in one transaction
- [ ] Chunk IDs are deterministic: same source text + config → same `chunk_id`
- [ ] `chunks` table stores `source_type` and `location_confidence`
- [ ] Search index only uses validated Markdown by default
- [ ] `.txt` fallback chunks are marked `location_confidence = "low"` with null
      `page_start`/`page_end`
- [ ] `--force` clears chunks + FTS only; embeddings cleared only with
      `--force --embeddings`; orphaned `chunk_embeddings` always cleaned up

**Embeddings**
- [ ] `paperlib embed` exits with actionable message if `chunks` table is empty
- [ ] Missing `sentence-transformers` produces install instruction and exits
- [ ] `paperlib embed` stores float32 embeddings (dim=384) in SQLite with `source_hash` and
      `dimension`
- [ ] Semantic search skips embeddings whose `source_hash` does not match
      current source text hash (stale vector protection)

**Search**
- [ ] `paperlib search "query"` defaults to hybrid mode
- [ ] FTS5 queries built through a safe query builder; syntax-sensitive physics
      terms escaped without collapsing multi-word expansions into single phrases
- [ ] `paperlib search "query" --json` produces stable JSON with `source_type`,
      `location_confidence`, `page_start`, `page_end`, `snippet`, score breakdown
- [ ] Alias expansion shown in terminal output and `--json`
- [ ] Keyword search works without embeddings (graceful fallback)
- [ ] Hybrid search works with `.txt` fallback when no Markdown exists, marking
      chunks as `location_confidence = "low"`
- [ ] Hybrid search degrades gracefully if embeddings are missing (semantic
      component skipped, warning printed, keyword + fuzzy still returned)
- [ ] Semantic mode exits non-zero with actionable message if no embeddings found

**Quality**
- [ ] `rebuild-search-index` is idempotent
- [ ] All new code covered by tests (including degraded-mode and stale-embedding
      scenarios)
- [ ] `config.example.toml` updated with all new keys
- [ ] CHANGELOG entry written
