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
    aliases.py                   # domain alias expansion
    chunking.py                  # Markdown/txt → Chunk list with section/page metadata
    index.py                     # rebuild search database (orchestrator)
    fts.py                       # SQLite FTS5 keyword search
    fuzzy.py                     # RapidFuzz over metadata fields
    embeddings.py                # embed text, store/load vectors from SQLite
    ranking.py                   # combine signals → ranked results
    service.py                   # high-level search(query, mode) → SearchResult[]
    data/
      __init__.py
      search_aliases.toml        # bundled default alias file; loaded via importlib.resources
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
    def extract(self, pdf_path: Path, config: AppConfig) -> MarkdownExtractionResult:
        ...
```

### Physics summary fields — add early

Add `phenomena`, `quantities`, and `aliases` to `_default_summary()["physics"]` in
[`models/record.py`](src/paperlib/models/record.py) **in this phase**, not in
Phase 4. The `_merge_dict` function only passes through keys present in defaults,
so these fields would be silently dropped on any read/write cycle until the
defaults are updated. Three-line change; zero behavioral risk on existing records.

`page_count` in `MarkdownExtractionResult`: the provider may return `None`.
The orchestrator computes `page_count` locally from the PDF (e.g. via
`pdfplumber`) and overwrites `result.page_count` before validation and
persistence. API-reported page counts must not be trusted.

---

## Phase 2 — Direct PDF → API Markdown extractor

**Goal:** produce `text/<paper_id>.md` from the original PDF via the AI API.

### Phase 2A — Extend `ai/client.py` with PDF/file input

`ai/client.py` is function-based (no client class). Add a new module-level
function following the existing `call_ai` / `call_openai_compatible` pattern:

```python
def call_ai_with_pdf(
    pdf_path: Path, prompt: str, model: str, ai_config
) -> str:  # raw Markdown string
```

`model` is an explicit parameter (not read from `ai_config`) because markdown
extraction uses `config.extraction.markdown_model`, which is a different config
key from the summarisation model at `config.ai.model`. `ai_config` carries only
API key env, base URL, temperature, max_tokens, and timeout.

Internally, `call_ai_with_pdf` calls `split_model_string(model)` to parse the
provider prefix and route to the correct backend — identical to how `call_ai`
routes. Provider validation (any provider other than `openai` or `openai-compat`
→ raise `AIError`) lives inside `call_ai_with_pdf`, not in the extractor.

All provider-specific details (base64 encoding, multipart upload, file object
API) live inside `call_ai_with_pdf`, not inside the extractor. `openai_pdf.py`
calls `call_ai_with_pdf()` and knows nothing about the wire format.

Providers that do not support PDF input must raise a clear `AIError`, for
example: `"anthropic provider does not support PDF input; use openai or
openai-compat"`. Only `openai` and `openai-compat` are accepted in v1.3.
`anthropic` raises immediately. `openrouter` is also rejected — if OpenRouter
is needed for PDF extraction, configure it as `openai-compat` with
`base_url = "https://openrouter.ai/api/v1"` and use a model that supports
file input; this makes the model-dependent behaviour explicit to the user.

### Phase 2B — Provider implementation

`pipeline/markdown_extractors/openai_pdf.py` — first and only implementation
for v1.3. Calls `call_ai_with_pdf(pdf_path, prompt, config.extraction.markdown_model, ai_config)`.
It passes `config.extraction.markdown_model` explicitly as `model`; it never
reads `ai_config.model` or `config.ai.model`.

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
paperlib extract-markdown --dry-run     # resolve candidates, skip AI calls and all writes
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

Add `_migrate_to_v3(conn)` called from `apply_migrations()` after
`_migrate_to_v2(conn)`. Bump `SCHEMA_VERSION = 3`. Follow the existing
`_migrate_to_vN` pattern — no registry, no dict.

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
-- No FK to chunks: --force rebuilds chunks (DELETE+INSERT) while preserving
-- embeddings unless --embeddings is passed. Orphan cleanup is handled
-- explicitly in index.py after each rebuild.
CREATE TABLE chunk_embeddings (
    chunk_id     TEXT PRIMARY KEY,
    paper_id     TEXT NOT NULL,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,  -- vector length; detect shape mismatch on load
    vector       BLOB NOT NULL,
    source_hash  TEXT NOT NULL,     -- sha256 of chunk text; skip re-embed if unchanged
    created_at   TEXT NOT NULL
);

-- Search index build state. At most one row (id = 1 enforced by CHECK).
-- Populated by rebuild-search-index; used by `paperlib search` to detect
-- an unbuilt index without relying on paper_fts row count (which is 0 for a
-- legitimately empty library after a successful rebuild).
CREATE TABLE search_index_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    built_at      TEXT NOT NULL,
    record_count  INTEGER NOT NULL,
    chunk_count   INTEGER NOT NULL
);
```

---

### Compatibility with `paperlib rebuild-index`

The existing `rebuild-index` command calls `_clear_index_tables(conn)` in
`store/db.py` before repopulating the base tables from JSON records. With v3,
`chunks.paper_id` and `paper_embeddings.paper_id` have FK references to
`papers`. Because `PRAGMA foreign_keys = ON` is always set in `connect()`,
deleting from `papers` before `chunks` or `paper_embeddings` raises a
constraint error.

`_clear_index_tables` must be updated to clear v3 search artifacts first:

```python
def _clear_index_tables(conn: sqlite3.Connection) -> None:
    # v3 search artifacts — cleared before base tables due to FK constraints.
    # paper_fts is standalone FTS5: plain DELETE works.
    # chunk_fts is external-content FTS5: do NOT DELETE FROM chunk_fts;
    # instead, after deleting chunks, run INSERT INTO chunk_fts(chunk_fts)
    # VALUES('rebuild') to produce an empty, internally consistent FTS index.
    # Both operations must run inside the same transaction as the table clears.
    # search_index_state cleared so `paperlib search` does not report the index
    # as built after rebuild-index discards search artifacts.
    for table in ("paper_fts", "chunk_embeddings", "paper_embeddings", "chunks",
                  "search_index_state"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
    # Base tables
    for table in ("processing_runs", "aliases", "files", "papers"):
        conn.execute(f"DELETE FROM {table}")
```

`paperlib rebuild-index` is allowed to discard search artifacts and embeddings
— all SQLite content is rebuildable from JSON records and text/Markdown files.
Embedding preservation semantics (`source_hash` matching) apply only to
`rebuild-search-index`, not to `rebuild-index`.

### `delete_paper` update required

`db.py:delete_paper()` currently deletes in the order `processing_runs →
aliases → files → papers`. With v3 FKs (`chunks.paper_id → papers`,
`paper_embeddings.paper_id → papers`) and `PRAGMA foreign_keys = ON`,
deleting from `papers` while child rows exist raises a constraint error.

Update `delete_paper` to clear v3 search artifacts for the paper before
deleting the base row:

```python
def delete_paper(conn: sqlite3.Connection, paper_id: str) -> None:
    try:
        conn.execute("BEGIN")
        # v3 search artifacts (FK children of papers)
        conn.execute(
            "DELETE FROM chunk_embeddings WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM paper_embeddings WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM chunks WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM paper_fts WHERE paper_id = ?", (paper_id,)
        )
        # Base tables
        conn.execute(
            "DELETE FROM processing_runs WHERE paper_id = ?", (paper_id,)
        )
        conn.execute("DELETE FROM aliases WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM files WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

`chunk_fts` is an external-content FTS5 table driven by `chunks`; deleting
from `chunks` automatically orphans the FTS entries, and the next
`INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')` cleans them up.
`search_index_state` is not per-paper, so it is left untouched by
`delete_paper`.

---

## Phase 6 — Chunking

**Goal:** split full text into overlapping chunks carrying source quality
metadata.

`search/chunking.py` — `chunk_document(record, config) -> list[Chunk]`

### File selection (multi-file records)

`PaperRecord.files` may contain more than one `FileRecord`. Select the single
file to chunk as follows:

1. If `summary.source_file_hash` is set and a `FileRecord` with that hash
   exists and has `extraction.status == "ok"` **and `word_count > 0`**, use
   that file. The `word_count > 0` guard prevents selecting a designated source
   file that is effectively empty (e.g. a scanned PDF that was never OCR'd).
2. Otherwise, sort candidates by:
   - extraction status: `"ok"` > `"partial"` > `"failed"` / `"pending"`
   - then `word_count` descending. Files with `word_count = 0` sort below any
     file with words, regardless of quality label.
   - then extraction quality: `"good"` > `"equation_heavy"` > `"low_text"` >
     `"unknown"` > `"scanned"`. `scanned` is last because scanned PDFs
     commonly produce zero or near-zero extractable text even when `word_count`
     is non-zero; `equation_heavy` is above `low_text` because it has usable
     prose around the equations.
   Use the first result.

Only one file is chunked per paper. The selected `file_hash` is recorded on
all generated `Chunk` objects (already in the schema).

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
paperlib rebuild-search-index --dry-run     # compute chunk counts, skip all DB writes
```

**`--force` semantics:**

| Flags | Chunks + FTS | Embeddings |
|---|---|---|
| _(none)_ | Fully rebuilt for all current papers | Preserved; orphans cleaned |
| `--embeddings` | Fully rebuilt | Rebuilt for missing/stale; orphans cleaned |
| `--force` | Fully rebuilt | Wiped entirely before rebuild; no re-embed |
| `--force --embeddings` | Fully rebuilt | Wiped then fully re-embedded |

Default (no `--force`) always replaces all chunks and FTS — there is no
incremental skip in v1.3. `--force` adds only one thing: it deletes
`paper_embeddings` and `chunk_embeddings` before the rebuild starts, giving a
guaranteed clean slate. Without `--force`, existing embeddings whose
`source_hash`, `model`, and `dimension` all match are preserved; orphaned rows
are always cleaned unconditionally.

`search/index.py` orchestrates:

1. Collect `current_paper_ids` from the full JSON record set.
2. Inside **one transaction** covering all papers:
   a. Delete stale and all chunks depending on library state:
      - If `current_paper_ids` is non-empty:
        ```sql
        DELETE FROM chunks WHERE paper_id NOT IN (current_paper_ids);
        ```
      - If `current_paper_ids` is empty (library has no records):
        ```sql
        DELETE FROM chunks;
        ```
      Both cases then proceed to the per-paper loop (which runs zero times for
      an empty library), so the FTS rebuild produces a consistent empty index.
   b. For each paper: delete its existing chunks, then insert newly computed
      chunks:
      ```sql
      DELETE FROM chunks WHERE paper_id = ?;
      INSERT INTO chunks ...;
      ```
   c. Rebuild `chunk_fts` **once** after all chunk inserts are complete:
      ```sql
      INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild');
      ```
      A single rebuild is correct and far cheaper than one rebuild per paper.
      This requires that `chunks.id` (the `content_rowid`) is always in sync.
3. Populate `paper_fts`: remove stale rows, then reinsert for all current
   papers from JSON records directly (not from the `papers` SQL table):
   - If `current_paper_ids` is non-empty:
     ```sql
     DELETE FROM paper_fts WHERE paper_id NOT IN (current_paper_ids);
     ```
   - If `current_paper_ids` is empty:
     ```sql
     DELETE FROM paper_fts;
     ```
4. **Always** delete orphaned `chunk_embeddings` unconditionally after chunk
   rebuild — rows whose `chunk_id` no longer exists in `chunks`, or whose
   `paper_id` no longer exists in `papers` (covers deleted records even if
   their chunk rows were already gone):
   ```sql
   DELETE FROM chunk_embeddings
   WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)
      OR paper_id NOT IN (SELECT paper_id FROM papers);
   ```
   Emit `ProgressKind.ORPHAN_CLEANED` with the count of rows deleted.
5. Upsert `search_index_state` (id=1) with `built_at`, `record_count` (len of
   current JSON set), and `chunk_count` (total rows in `chunks`).
6. If `--embeddings`: call the same embedding logic used by `paperlib embed`
   for each paper and chunk that lacks a current embedding.

`paperlib rebuild-search-index --embeddings` and `paperlib embed` use the same
underlying code. The difference: `rebuild-search-index` always rebuilds chunks
and FTS first; `paperlib embed` skips chunk/FTS and updates embeddings only.
Running both is safe but redundant — use `rebuild-search-index --embeddings`
when rebuilding from scratch, `paperlib embed` for incremental updates.

Library functions (`index.py`, `embeddings.py`) yield structured `ProgressEvent`
objects. CLI commands consume these and format them via `click.echo`. All
user-facing wording lives only in `cli.py` — library code never calls `print`
or `click.echo`. No external progress bar dep.

```python
from enum import StrEnum
from dataclasses import dataclass

class ProgressKind(StrEnum):
    PAPER_START       = "paper_start"       # beginning to process a paper
    PAPER_DONE        = "paper_done"        # paper processing complete
    CHUNKS_WRITTEN    = "chunks_written"    # chunk batch written for one paper
    FTS_REBUILT       = "fts_rebuilt"       # FTS tables rebuilt
    EMBED_START       = "embed_start"       # beginning paper-level embedding
    EMBED_DONE        = "embed_done"        # paper-level embedding stored
    CHUNK_EMBED_START = "chunk_embed_start" # beginning chunk-level embedding
    CHUNK_EMBED_DONE  = "chunk_embed_done"  # chunk-level embedding stored
    ORPHAN_CLEANED    = "orphan_cleaned"    # orphaned chunk_embeddings removed

@dataclass(frozen=True)
class ProgressEvent:
    kind:      ProgressKind
    paper_id:  str | None = None
    handle_id: str | None = None
    count:     int | None = None
    total:     int | None = None
    status:    str | None = None
```

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

Loads bundled aliases from `paperlib.search.data/search_aliases.toml` via
`importlib.resources`. `search.alias_file` config key may override this with a
user-provided TOML file. Maps abbreviation or variant → list of canonical forms.
Applied to queries at search time — not stored in the index.

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

If `sentence-transformers` is not installed (broken environment), exit with:
```
Error: sentence-transformers is not installed. Reinstall paperlib: pip install -e .
```
Note: `sentence-transformers` is a required dep — there is no `[search]`
optional extra. This error should not occur in a normal install.

### Paper embedding source text

Concatenation of: `title | authors | summary.short | summary.technical | tags |
materials | devices | phenomena | quantities | aliases | methods`

### Chunk embedding source text

`{section_title}: {chunk_text}` (section title prefix improves chunk retrieval).

### Incremental update

Skip embed if **all three** of `source_hash` (sha256 of source text), `model`,
and `dimension` match the stored values. A change in any one triggers
re-embedding. This ensures that switching embedding models or any model that
produces a different vector dimension always invalidates stored vectors —
reusing vectors from a different model would silently corrupt similarity scores.

### CLI

```
paperlib embed                   # embed all missing
paperlib embed --force           # re-embed all
paperlib embed --backend local   # override config
paperlib embed --dry-run         # report missing/stale counts, skip model load and DB writes
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
3. Load `paper_embeddings`, skipping any row where `source_hash`, `model`, or
   `dimension` does not match the current values. All three must match; a model
   or dimension mismatch means the vector is incompatible even if the source
   text is unchanged. Stale vectors must not be used — they survive rebuilds
   where chunks changed but `--embeddings` was not passed.
4. Cosine similarity vs. loaded paper vectors → top-K paper scores.
5. Load `chunk_embeddings`, skipping any row where `source_hash`, `model`, or
   `dimension` does not match the current `chunks.text_hash` and configured
   embedding model/dimension.
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
paperlib search "query" --field title    # optional: restrict FTS/fuzzy to one field
paperlib search "query" --sort year      # optional: re-sort results (default: by score)
```

### Empty index guard

If `rebuild-search-index` has never run when `paperlib search` is called, the
command must exit with a clear actionable message and non-zero status rather
than silently returning zero results:

```
Error: search index not built. Run `paperlib rebuild-search-index` first.
```

Do **not** fall back to the legacy LIKE search — the result set would be
qualitatively different from FTS/hybrid results and would confuse users who
just upgraded. Detect by checking for a row in `search_index_state` (not by
`SELECT COUNT(*) FROM paper_fts`): an empty `paper_fts` is valid for a library
with zero papers after a successful rebuild, so a row count of 0 is not a
reliable signal. If no row exists in `search_index_state`, the index has never
been built.

**Backward compatibility:** The existing `--field title|authors|summary|all`
flag is retained as an optional filter layered on top of `--mode`, not as a
replacement for it. `--sort` is kept but results default to relevance/score
ordering; `--sort` is only applied when explicitly passed. No deprecation
warnings — the interface change is additive. Tests and docs referencing `--field`
or `--sort` are updated to reflect this layered behaviour.

### `--field` mapping

`--field` restricts FTS column filtering and fuzzy field targeting. Semantic
search **always ignores `--field`** and operates over full embeddings — passing
`--field title --mode semantic` is accepted without error but `--field` has no
effect on the semantic component. This behaviour must be documented in `--help`.

| `--field` | `paper_fts` columns queried | Fuzzy fields | Chunk FTS |
|---|---|---|---|
| `title` | `title` only | title only | disabled |
| `authors` | `authors` only | authors only | disabled |
| `summary` | `summary_short`, `summary_technical` | none | enabled |
| `all` | all columns (default) | all fields | enabled |

`--field summary` in v1.3 uses FTS5 on `summary_short`/`summary_technical`
columns — the current JSON scan implementation is replaced. The CLI interface
is unchanged.

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
markdown_model    = "openai:gpt-5.4"  # prefix required; only "openai" or "openai-compat" accepted
markdown_require_validation    = true
markdown_fallback_to_txt       = true
markdown_fallback_partial      = false   # use "partial" validated markdown

[search]
embedding_backend  = "local"
embedding_model    = "sentence-transformers/all-MiniLM-L6-v2"
alias_file         = ""                  # blank → uses bundled src/paperlib/search/data/search_aliases.toml
default_mode       = "hybrid"            # "keyword" | "fuzzy" | "semantic" | "hybrid"
top_n              = 10
```

### Python dataclass changes (`config.py`)

Extend `ExtractionConfig` with the new fields and add `SearchConfig`. Update
`AppConfig` to include `search`. Update `load_config` to read both sections.

```python
@dataclass
class ExtractionConfig:           # extends existing
    engine: str
    min_char_count: int
    min_word_count: int
    markdown_backend: str         # "openai_pdf" | "none"; default "none"
    markdown_model: str           # e.g. "openai:gpt-5.4"; provider prefix required
    markdown_require_validation: bool    # default True
    markdown_fallback_to_txt: bool       # default True
    markdown_fallback_partial: bool      # default False


@dataclass
class SearchConfig:               # new
    embedding_backend: str        # "local"; default "local"
    embedding_model: str          # default "sentence-transformers/all-MiniLM-L6-v2"
    alias_file: str               # "" = use bundled; non-empty = user path override
    default_mode: str             # "hybrid"; "keyword"|"fuzzy"|"semantic"|"hybrid"
    top_n: int                    # default 10


@dataclass
class AppConfig:                  # add search field
    library: LibraryConfig
    paths: PathsConfig
    pipeline: PipelineConfig
    extraction: ExtractionConfig
    ai: AIConfig
    lookup: LookupConfig
    search: SearchConfig          # new
```

`load_config` reads `_section(data, "search")` and constructs `SearchConfig`
with the defaults shown above. `markdown_model` validation at config load is
stricter than `ai.model`: (1) a provider prefix is required — unprefixed strings
do **not** route to Anthropic and instead raise `ConfigError`; (2) the provider
must be `openai` or `openai-compat` — any other value (including `anthropic`,
`openrouter`) raises `ConfigError`. This matches the runtime constraint in
`call_ai_with_pdf` and surfaces misconfiguration immediately on startup rather
than at extraction time.

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
  `source_type` / `location_confidence` for Markdown vs. txt source;
  file-selection: equation-heavy file with words beats scanned/empty file;
  file-selection: `source_file_hash` target with `word_count = 0` falls through
  to fallback sort
- `test_normalize.py` — hyphen variants, alias expansion, round-trip
- `test_aliases.py` — bundled aliases load via `importlib.resources`; `search.alias_file`
  override loads from user-provided TOML file instead
- `test_fts.py` — exact title match, author match, chunk hit, no-result case
- `test_fuzzy.py` — typo cases (resnator, feromagnetic, bogolubov)
- `test_semantic.py` — mock embeddings (cosine sim math only, no model load)
- `test_ranking.py` — weight formula, multi-concept bonus, score cap
- `test_index_rebuild.py` — idempotency (run twice, same chunk and FTS state);
  regression: `paperlib rebuild-index` succeeds when v3 search tables and
  embeddings already exist; asserts `chunk_fts` is empty and consistent
  (not stale) after rebuild-index clears and repopulates base tables
- `test_search_json.py` — JSON schema validation against known fixture query
- `test_search_degraded.py` — hybrid search with no embeddings emits warning
  line and returns keyword + fuzzy results; semantic mode exits non-zero;
  `paperlib search` with no `search_index_state` row exits non-zero with
  "search index not built" message; empty `paper_fts` after a successful
  rebuild (zero papers) does NOT trigger the error
- `test_config.py` — `SearchConfig` loaded with correct defaults; extended
  `ExtractionConfig` fields parsed correctly; `markdown_model` with missing
  prefix raises `ConfigError`; `markdown_model` with `anthropic:` or
  `openrouter:` prefix raises `ConfigError`; `openai-compat:` prefix accepted;
  `search.alias_file` override accepted; `AppConfig.search` present
- `test_embeddings_invalidation.py` — changing `embedding_model` config causes
  re-embed on next run (source_hash unchanged, model changed → mismatch);
  changing dimension (different model) also invalidates; both paper and chunk
  embeddings tested
- `test_stale_chunks.py` — after deleting a JSON record and running
  `rebuild-search-index`, chunks and chunk_embeddings for that paper_id are
  removed; `search_index_state.record_count` reflects the new count
- `test_ai_client.py` — `call_ai_with_pdf` calls `split_model_string(model)`
  internally; `markdown_model = "openai:gpt-5.4"` routes to OpenAI backend;
  Anthropic provider string raises `AIError` immediately
- `test_progress_events.py` — `index.py`, `embeddings.py` yield only
  `ProgressEvent` objects (never call `print` or `click.echo`); `kind` values
  are members of `ProgressKind`; `EMBED_START`/`EMBED_DONE` and
  `CHUNK_EMBED_START`/`CHUNK_EMBED_DONE` are distinct and correctly emitted

---

## Dependencies

New runtime deps:

| Package | Required? | Purpose |
|---|---|---|
| `rapidfuzz` | **Required** | Fuzzy string matching |
| `numpy` | **Required** | Vector math for embeddings |
| `sentence-transformers` | **Required** | Local embedding model (~90 MB download on first run) |

Search and embeddings are standard paperlib features, not an optional extra.
All three packages ship as required dependencies.

```toml
# pyproject.toml
dependencies = [
    "rapidfuzz",
    "numpy",
    "sentence-transformers",
    ...
]

[tool.setuptools.package-data]
"paperlib.search.data" = ["*.toml"]
```

The `search/data/` directory must contain an `__init__.py` so setuptools
recognises it as a package. The alias file is loaded at runtime via
`importlib.resources.files("paperlib.search.data").joinpath("search_aliases.toml")`.

Install: `pip install -e .` (dev) or `pip install paperlib` (published). No
extra step required — the embedding model downloads on first use (~90 MB from
Hugging Face) and is cached locally thereafter.

---

## Migration notes (for CHANGELOG)

After upgrading to v1.3:

1. `rapidfuzz`, `numpy`, and `sentence-transformers` are all required deps —
   `pip install paperlib` or `pip install -e .` installs everything. No extra
   `[search]` step. The embedding model (~90 MB) downloads from Hugging Face on
   first use of `embed` or `rebuild-search-index --embeddings`.
2. Optional: `paperlib extract-markdown --all` — produces `.md` files for all
   papers. Requires AI API key; costs API calls per PDF. **Run this before
   rebuilding the index** so the chunker uses high-confidence Markdown. Without
   this, chunking falls back to `.txt` with low location confidence.
3. `paperlib rebuild-search-index` — builds chunks and FTS tables. Fast; no
   embeddings. Keyword and fuzzy search are usable after this step.
4. Optional: `paperlib embed` — computes embeddings (downloads model on first
   run for local backend). Required for semantic mode. Hybrid search degrades
   gracefully without embeddings — the semantic component is skipped and a
   warning is printed; keyword and fuzzy results are still returned.

Recommended workflow after upgrade:
```
paperlib extract-markdown --all
paperlib rebuild-search-index --embeddings
paperlib search "ferromagnetic hybrid resonator"
```

---

## Definition of done (v1.3)

**Extraction**
- [ ] `ai/client.py` extended with module-level `call_ai_with_pdf(pdf_path, prompt, model, ai_config)` (Phase 2A); calls `split_model_string(model)` internally; `openai_pdf.py` passes `config.extraction.markdown_model` as `model`
- [ ] `call_ai_with_pdf` raises actionable `AIError` for Anthropic/unsupported providers
- [ ] `MarkdownExtractionResult` and `MarkdownInfo` dataclasses implemented
- [ ] `_default_summary()["physics"]` includes `phenomena`, `quantities`, `aliases` (Phase 1)
- [ ] `MarkdownExtractor` Protocol defined with `config: AppConfig`; `openai_pdf` provider implemented
      via `call_ai_with_pdf()` — no wire-format details in the extractor
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
- [ ] `extract-markdown --dry-run` resolves candidates, skips AI calls and all writes
- [ ] Re-running `extract-markdown` is idempotent unless `--force` is used

**Indexing**
- [ ] Migration follows `_migrate_to_v3(conn)` pattern; `SCHEMA_VERSION = 3`
- [ ] `_clear_index_tables` updated: deletes `paper_fts`, `chunk_embeddings`, `paper_embeddings`, `chunks`, `search_index_state`, runs `INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')` (inside the transaction), then clears base tables — so `rebuild-index` leaves `chunk_fts` empty and `search_index_state` cleared
- [ ] `delete_paper` updated: clears `chunk_embeddings`, `paper_embeddings`, `chunks`, `paper_fts` for the paper before deleting from `papers` — satisfies v3 FK constraints with `PRAGMA foreign_keys = ON`
- [ ] `paperlib rebuild-search-index` builds schema v3, chunks, FTS
- [ ] `rebuild-search-index --dry-run` computes chunk counts, skips all DB writes
- [ ] `chunk_embeddings` has no FK to `chunks`; orphan cleanup runs unconditionally (by chunk_id AND paper_id), not gated on `--embeddings`
- [ ] Stale chunks for deleted JSON records removed at start of rebuild (`DELETE FROM chunks WHERE paper_id NOT IN current_ids`)
- [ ] `chunk_fts` rebuilt once per `rebuild-search-index` run (not once per paper)
- [ ] `search_index_state` upserted after every successful rebuild with `built_at`, `record_count`, `chunk_count`
- [ ] Chunker selects file via `summary.source_file_hash` first, then status/quality/word_count
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
- [ ] `sentence-transformers` is a required dep; no optional `[search]` extra
- [ ] `embed --dry-run` reports missing/stale counts, skips model load and DB writes
- [ ] `paperlib embed` stores float32 embeddings (dim=384) in SQLite with `source_hash` and
      `dimension`
- [ ] Semantic search skips embeddings where `source_hash`, `model`, or `dimension` does not match; all three must agree

**Search**
- [ ] `paperlib search "query"` defaults to hybrid mode
- [ ] FTS5 queries built through a safe query builder; syntax-sensitive physics
      terms escaped without collapsing multi-word expansions into single phrases
- [ ] `paperlib search "query" --json` produces stable JSON with `source_type`,
      `location_confidence`, `page_start`, `page_end`, `snippet`, score breakdown
- [ ] Alias expansion shown in terminal output and `--json`
- [ ] `paperlib search` detects missing `search_index_state` row and exits non-zero with "search index not built" message; no LIKE fallback; empty `paper_fts` with a valid state row (zero-paper library) is not an error
- [ ] `--field` restricts FTS columns and fuzzy fields per mapping table; semantic search ignores `--field`; `--help` documents this
- [ ] `--sort` retained; default ordering is relevance/score (not year)
- [ ] Keyword search works without embeddings (graceful fallback)
- [ ] Hybrid search works with `.txt` fallback when no Markdown exists, marking
      chunks as `location_confidence = "low"`
- [ ] Hybrid search degrades gracefully if embeddings are missing (semantic
      component skipped, warning printed, keyword + fuzzy still returned)
- [ ] Semantic mode exits non-zero with actionable message if no embeddings found

**Quality**
- [ ] `rebuild-search-index` is idempotent
- [ ] Library functions yield structured `ProgressEvent(kind=ProgressKind.*, ...)` objects; `kind` is always a `ProgressKind` member; all `click.echo` calls live in `cli.py`; no `print` in library code
- [ ] `search/data/search_aliases.toml` loaded via `importlib.resources`; `alias_file` config key overrides
- [ ] `[tool.setuptools.package-data]` includes `paperlib.search.data = ["*.toml"]`
- [ ] All new code covered by tests (including degraded-mode and stale-embedding
      scenarios)
- [ ] `ExtractionConfig` extended with markdown fields; `SearchConfig` added; `AppConfig.search` wired in `load_config`
- [ ] `markdown_model` validated at config load: prefix required; only `openai` or `openai-compat` accepted; `anthropic`, `openrouter`, unprefixed all raise `ConfigError`
- [ ] `config.example.toml` updated with all new keys (including `openai:` prefix on `markdown_model`)
- [ ] `docs/config.md` updated with all v1.3 config keys, defaults, and valid values
- [ ] `docs/operations.md` updated with upgrade workflow and `rebuild-search-index` / `embed` commands
- [ ] `docs/schema.md` updated with v3 SQLite tables and JSON shape changes (`MarkdownInfo` in `FileRecord`)
- [ ] CHANGELOG entry written
