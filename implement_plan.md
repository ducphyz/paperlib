## `paperlib` — Implementation Plan

This is the single reference document for building `paperlib` v1. It consolidates all prior design iterations and feedback. Do not revisit design decisions during implementation unless a concrete coding obstacle demands it.

---

## Part A — Fixed Specifications

These are frozen. Do not modify during implementation.

### A.1 Scope

**In scope for v1:**
- Scan `inbox/` for PDFs
- Hash, deduplicate, validate, extract text, clean text
- Detect DOI and arXiv ID via regex
- Assign stable internal `paper_id`
- Generate canonical filename
- Move PDF to `papers/`, write text to `text/`, write JSON to `records/`
- Update SQLite index
- Optional AI metadata + structured summary via Anthropic
- CLI: `validate-config`, `ingest`, `status`, `show`, `list`, `rebuild-index`

**Out of scope:** OCR, external API lookups (Crossref, arXiv API, Semantic Scholar), RAG, embeddings, chunking, page-level JSONL, GUI, multi-provider AI, fuzzy duplicate detection.

### A.2 Core Rules

| Rule | Value |
|---|---|
| `paper_id` | `p_{sha256_of_first_file[:16]}`, stable, never changes |
| Source of truth | JSON canonical, SQLite rebuildable |
| On conflict | JSON wins |
| Canonical filename | `{year}_{first_author}_{hash8}.pdf`, with `unknown_year` / `unknown_author` fallbacks |
| PDF move | Before JSON write |
| Atomic writes | tempfile + fsync + rename for all text/JSON |
| API key | Required only when `ai.enabled=true` AND command uses AI |
| Locked fields | Never overwritten on rerun |
| Fabrication | Forbidden — unknown fields are `null` |

### A.3 Repo Structure

```
paperlib/
├── README.md
├── pyproject.toml
├── config.example.toml
├── .env.example
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── schema.md
│   ├── config.md
│   ├── operations.md
│   ├── limitations.md
│   └── roadmap.md
├── src/paperlib/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── logging_config.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── status.py
│   │   ├── metadata.py
│   │   ├── file.py
│   │   ├── identity.py
│   │   └── record.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── discover.py
│   │   ├── validate.py
│   │   ├── extract.py
│   │   ├── clean.py
│   │   ├── metadata.py
│   │   ├── summarise.py
│   │   └── ingest.py
│   ├── store/
│   │   ├── __init__.py
│   │   ├── fs.py
│   │   ├── json_store.py
│   │   ├── db.py
│   │   └── migrations.py
│   └── ai/
│       ├── __init__.py
│       ├── client.py
│       └── prompts.py
└── tests/
    ├── __init__.py
    ├── fixtures/README.md
    ├── test_discover.py
    ├── test_validate.py
    ├── test_extract.py
    ├── test_clean.py
    ├── test_metadata.py
    ├── test_identity_resolution.py
    ├── test_fs.py
    ├── test_json_store.py
    ├── test_db.py
    └── test_ingest_idempotency.py
```

### A.4 Runtime Layout

```
{library_root}/
├── inbox/
├── papers/{year}/{year}_{first_author}_{hash8}.pdf
├── records/{paper_id}.json
├── text/{file_hash16}.txt
├── db/library.db
├── logs/ingest.log
├── failed/
└── duplicates/
```

### A.5 Status Enums

```
ExtractionStatus  : pending | ok | partial | failed
ExtractionQuality : good | low_text | scanned | equation_heavy | unknown
MetadataStatus    : pending | ok | partial | needs_review | failed
SummaryStatus     : pending | generated | failed | skipped
DuplicateStatus   : unique | exact_duplicate | alias_duplicate
ReviewStatus      : needs_review | reviewed
MetadataSource    : pdf_embedded_meta | pdf_text | filename | ai | user
```

### A.6 Full JSON Schema (v1)

```json
{
  "schema_version": 1,
  "paper_id": "p_abc123def4567890",
  "identity": {
    "doi": "10.1103/physrevlett.xxx",
    "arxiv_id": "2401.12345",
    "aliases": ["hash:abc123def4567890", "arxiv:2401.12345", "doi:10.1103/physrevlett.xxx"]
  },
  "files": [
    {
      "file_hash": "abc123def4567890abcdef",
      "original_filename": "paper.pdf",
      "canonical_path": "papers/2024/2024_smith_abc12345.pdf",
      "text_path": "text/abc123def4567890.txt",
      "size_bytes": 1234567,
      "added_at": "2026-04-24T10:00:00Z",
      "extraction": {
        "status": "ok",
        "engine": "pdfplumber",
        "engine_version": "0.11",
        "page_count": 12,
        "char_count": 43210,
        "word_count": 6100,
        "quality": "good",
        "warnings": []
      }
    }
  ],
  "metadata": {
    "title":   {"value": null, "source": null, "confidence": null, "locked": false, "updated_at": null},
    "authors": {"value": null, "source": null, "confidence": null, "locked": false, "updated_at": null},
    "year":    {"value": null, "source": null, "confidence": null, "locked": false, "updated_at": null},
    "journal": {"value": null, "source": null, "confidence": null, "locked": false, "updated_at": null}
  },
  "summary": {
    "status": "pending",
    "source_file_hash": null,
    "model": null,
    "prompt_version": null,
    "generated_at": null,
    "locked": false,
    "one_sentence": null,
    "short": null,
    "technical": null,
    "key_contributions": [],
    "methods": [],
    "limitations": [],
    "physics": {"field": null, "materials": [], "devices": [], "measurements": [], "main_theory": []},
    "tags": []
  },
  "status": {
    "metadata":  "pending",
    "summary":   "pending",
    "duplicate": "unique",
    "review":    "needs_review"
  },
  "review": {"notes": "", "locked": false},
  "timestamps": {"created_at": "...", "updated_at": "..."}
}
```

### A.7 Full SQLite Schema

```sql
CREATE TABLE papers (
    paper_id         TEXT PRIMARY KEY,
    title            TEXT,
    authors_json     TEXT,
    year             INTEGER,
    journal          TEXT,
    doi              TEXT,
    arxiv_id         TEXT,
    metadata_status  TEXT NOT NULL DEFAULT 'pending',
    summary_status   TEXT NOT NULL DEFAULT 'pending',
    duplicate_status TEXT NOT NULL DEFAULT 'unique',
    review_status    TEXT NOT NULL DEFAULT 'needs_review',
    record_path      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE aliases (
    alias       TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    alias_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE files (
    file_hash          TEXT PRIMARY KEY,
    paper_id           TEXT NOT NULL,
    original_name      TEXT,
    canonical_path     TEXT,
    text_path          TEXT,
    size_bytes         INTEGER,
    page_count         INTEGER,
    char_count         INTEGER,
    word_count         INTEGER,
    extraction_status  TEXT NOT NULL DEFAULT 'pending',
    extraction_quality TEXT,
    added_at           TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE processing_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash    TEXT,
    paper_id     TEXT,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    message      TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX idx_papers_doi     ON papers(doi);
CREATE INDEX idx_papers_arxiv   ON papers(arxiv_id);
CREATE INDEX idx_papers_year    ON papers(year);
CREATE INDEX idx_papers_review  ON papers(review_status);
CREATE INDEX idx_files_paper    ON files(paper_id);
CREATE INDEX idx_aliases_paper  ON aliases(paper_id);
```

### A.8 Ingest State Machine

```
For each PDF in inbox/:
  1.  DISCOVER       → SHA-256 hash, size, mtime
  2.  DEDUPLICATE    → if file_hash in files table: log skip, continue
  3.  VALIDATE       → readable / encrypted / zero-text
                       if invalid: move to failed/, processing_run, continue
  4.  EXTRACT        → pdfplumber → raw text + quality metrics
  5.  CLEAN          → whitespace, ligatures, control chars
  6.  IDENTIFY       → regex DOI + arXiv; resolve alias → existing paper_id,
                       or assign new p_{hash16}
  7.  DECIDE NAME    → {year}_{first_author}_{hash8}.pdf with fallbacks
  8.  MOVE PDF       → papers/{year}/{canonical_name}.pdf   ← before record writes
  9.  WRITE TEXT     → text/{hash16}.txt (atomic)
  10. METADATA       → per-field extraction; skip locked
  11. SUMMARISE      → AI call unless --no-ai or summary.locked
                       on failure: summary.status="failed", record still written
  12. WRITE JSON     → records/{paper_id}.json (atomic)
  13. UPDATE DB      → papers + aliases + files + processing_run (transaction)
  14. LOG
```

### A.9 Configuration Files

**`config.example.toml`**
```toml
[library]
root = "/Users/you/PaperLibrary"

[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
duplicates = "duplicates"

[pipeline]
move_after_ingest = true
skip_existing     = true
dry_run_default   = false

[extraction]
engine         = "pdfplumber"
min_char_count = 500
min_word_count = 100

[ai]
enabled     = true
provider    = "anthropic"
model       = "claude-sonnet-4-20250514"
max_tokens  = 1200
temperature = 0.2
```

**`.env.example`**
```
ANTHROPIC_API_KEY=sk-ant-...
```

**`pyproject.toml`**
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "paperlib"
version = "0.1.0"
requires-python = ">=3.14.3"
dependencies = [
    "click>=8.1",
    "pdfplumber>=0.11",
    "anthropic>=0.25",
    "python-dotenv>=1.0",
]

[project.scripts]
paperlib = "paperlib.cli:main"

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov"]
```

**`.gitignore`**
```
.env
config.toml
*.db
*.db-*
*.log
__pycache__/
.pytest_cache/
.venv/
dist/
build/
*.egg-info/
.DS_Store
PaperLibrary/
library/
runtime/
```

---

## Part B — Implementation Phases

Each phase has: **deliverables**, **key design notes**, **gate criterion**. Do not start a phase until its predecessor's gate passes.

---

### Phase 1 — Skeleton

**Deliverables**
- Full directory tree with empty `__init__.py` files
- `pyproject.toml`, `README.md` (stub), `config.example.toml`, `.env.example`, `.gitignore`
- All stub files in `docs/`
- `models/status.py` — all enums as plain string constants
- `models/metadata.py` — `MetadataField` dataclass
- `models/file.py` — `ExtractionInfo`, `FileRecord` dataclasses
- `models/identity.py` — `PaperIdentity` dataclass
- `models/record.py` — `PaperRecord` dataclass with `to_dict()` and `from_dict()` helpers
- `config.py` — TOML + .env loader, `AppConfig` dataclass with resolved absolute paths
- `logging_config.py` — file + console logger, never logs API keys
- `store/fs.py` — for now only `ensure_runtime_dirs(config)` and `sha256_file(path)`
- `cli.py` — click entry point with `validate-config` command only

**Key design notes**

Use plain dataclasses, not Pydantic. Model imports must not fail at startup.

`sha256_file` must stream in 64 KB chunks. Do not read whole PDFs into memory.

`config.py` resolves all path keys relative to `library.root`. The resolved `AppConfig` holds `pathlib.Path` objects, not strings.

`validate-config` rules (frozen):
```
1. If library.root does not exist → print clear error, exit nonzero.
2. If library.root exists → create any missing subdirectories listed in [paths].
3. If ai.enabled = true → check ANTHROPIC_API_KEY present.
   If missing → warn but do not fail (non-AI commands still work).
4. Print a table: each path with ok/created/missing status.
```

This rule prevents silently creating a new library under a typo like `/Users/duc/PaperLibrarry`.

**Gate**
```
pip install -e ".[dev]"
paperlib validate-config
```
Prints path status and API key status. Exits cleanly on a valid config. Exits with error on missing `root`.

---

### Phase 2 — Discovery and Validation

**Deliverables**
- `pipeline/discover.py` — recursive PDF scan, hashing, `DiscoveredPDF` dataclass
- `pipeline/validate.py` — readability check, page count, text-presence estimate, `ValidationResult` dataclass
- `cli.py` — `ingest --dry-run` command: discover + validate, print table, no writes

**Key design notes**

`DiscoveredPDF` fields: `path`, `file_hash` (64 hex), `hash16` (first 16), `hash8` (first 8), `size_bytes`, `modified_time` (ISO 8601 UTC).

`discover.py` returns a list. It does not touch SQLite in this phase. Database-backed deduplication happens in Phase 5.

`validate.py` opens each PDF with `pdfplumber`, catches exceptions, samples the first 1–2 pages' text to detect zero-text PDFs. It must never raise — always return a `ValidationResult` with `ok=False` and `reason` set.

Dry-run table columns:
```
path | hash16 | size (KB) | pages | validation | reason
```

**Gate**

`paperlib ingest --dry-run` on a test inbox prints the full table. No files created or moved. Rerunning produces identical output. A broken PDF does not stop the run.

---

### Phase 3 — Extraction and Cleaning

**Deliverables**
- `pipeline/extract.py` — full-text extraction via pdfplumber, quality classification, `ExtractionResult` dataclass
- `pipeline/clean.py` — `clean_text(raw: str) -> str`
- `store/fs.py` — add `atomic_write_text(path, text)`
- `cli.py` — `ingest --limit N` writes `text/{hash16}.txt` for valid PDFs; dry-run still writes nothing

**Key design notes**

Extraction quality rules (frozen order, first match wins):
```
word_count == 0                         → scanned
char_count < config.extraction.min_char_count → low_text
word_count < config.extraction.min_word_count → low_text
replacement char ratio > 0.05            → equation_heavy
otherwise                                → good
```

Do not refine the `equation_heavy` heuristic. A crude flag is sufficient for v1.

`clean_text` operations, in this order:
```
1. Normalize line endings to \n
2. Remove ASCII control chars except \n and \t
3. Normalize ligatures: ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl
4. Collapse runs of ≥3 newlines to 2
5. Collapse runs of spaces/tabs to single space
6. Strip leading/trailing whitespace per line
```

Invalid PDFs discovered in Phase 2 are now moved to `failed/` in non-dry-run mode. Filename preserved.

**Gate**

`paperlib ingest --limit 3` writes `text/{hash16}.txt` for valid PDFs. Quality correctly classified on a test set. Broken PDFs moved to `failed/`. Batch completes through failures. `--dry-run` still writes nothing.

---

### Phase 4 — Identity and Metadata (Non-AI)

**Deliverables**
- `pipeline/metadata.py` — DOI regex, arXiv regex, year heuristic, metadata field construction
- `models/identity.py` — `normalize_doi`, `normalize_arxiv_id`, alias-list construction
- `store/fs.py` — `sanitize_component`, `canonical_pdf_relative_path`

**Key design notes**

**DOI normalization:**
```
- Strip leading https://doi.org/, http://dx.doi.org/, doi:
- Lowercase
- Strip trailing punctuation: . , ; )
- Preserve internal slashes
```

**arXiv normalization:**
```
- Accept forms: "arXiv:2401.12345", "2401.12345", "2401.12345v2", "cond-mat/0211034"
- Strip "arXiv:" prefix (case-insensitive)
- Strip version suffix "vN" for identity
- Keep full identifier format (slashes for old-style IDs)
```

**DOI regex (intentionally conservative):**
```
\b10\.\d{4,9}/[^\s]+\b
```
Apply to first 5000 chars; if no hit, scan full text. Take first match only.

**arXiv regex:**
```
(?:arXiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)
```
Plus an old-style fallback:
```
(?:arXiv:\s*)?([a-z\-]+/\d{7}(?:v\d+)?)
```
Search filename first, then first 5000 chars of text.

**Year heuristic (frozen — this replaces any earlier looser rule):**
```
Priority:
1. If arXiv ID matches YYMM.xxxxx:
     yy = int(YY)
     year = 2000 + yy  if yy <= (current_year % 100) else 1900 + yy
   (handles 1999 vs 2024 unambiguously)
2. Scan first 2000 chars of cleaned text for year patterns within ±80 chars
   of any keyword (case-insensitive): "received", "revised", "accepted",
   "published", "copyright", "©"
   Year must be in range [1900, current_year + 1]. Take first match.
3. Otherwise null.
```
Do not do a bare four-digit regex over the first page. It picks up reference years.

**Canonical filename (frozen):**
```
Components:
  year         → str(year) if present else "unknown_year"
  first_author → sanitize(authors[0]) if present else "unknown_author"
  hash8        → file_hash[:8]

Filename: {year}_{first_author}_{hash8}.pdf
Directory: papers/{year}/
```

**Sanitization (frozen):**
```python
import unicodedata

def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

def sanitize_component(s: str, max_len: int = 40) -> str:
    if not s:
        return ""
    s = ascii_fold(s).lower()
    # replace whitespace and separators with underscore
    s = re.sub(r"[\s/\\,;:]+", "_", s)
    # remove anything not a-z0-9_-
    s = re.sub(r"[^a-z0-9_-]", "", s)
    # collapse underscores
    s = re.sub(r"_+", "_", s).strip("_-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_-")
    return s
```

Empty input → caller substitutes `"unknown_author"` (keeps sanitizer pure).

**Alias construction:**
```
aliases = []
aliases.append(f"hash:{hash16}")
if arxiv_id: aliases.append(f"arxiv:{arxiv_id}")
if doi: aliases.append(f"doi:{doi}")
```

**Phase 4 metadata output:**

Without AI, this phase produces:
```
title:   null (will be filled by AI in Phase 6)
authors: null
journal: null
year:    value from arXiv ID or keyword heuristic, else null
doi:     detected or null
arxiv_id: detected or null
```

Each `MetadataField` records `source` (`pdf_text` for regex hits, `null` for nulls), `confidence` (0.95 for arXiv-derived year, 0.70 for keyword-derived year, `null` for null values), `locked=false`, `updated_at` ISO 8601.

**Gate**

On 10 test PDFs (mix of arXiv, journal, borderline): correct DOI and arXiv detection where present; `paper_id` stable across two runs on same file; missing fields are `null` with `source=null`; year extraction produces no false positives from reference lists.

---

### Phase 5 — Persistence

**Deliverables**
- `store/json_store.py` — atomic write, read, schema-version validation
- `store/db.py` — all SQL
- `store/migrations.py` — schema version 1 bootstrap
- `cli.py` — `rebuild-index` command
- `pipeline/ingest.py` — non-AI end-to-end orchestration (steps 1–10, 12–14 of state machine; step 11 stubbed)

**Key design notes**

**Atomic JSON write:**
```
1. Create parent dir if missing
2. Write to records/{paper_id}.json.tmp
3. fsync
4. os.replace(tmp, final)
```

JSON formatting: `json.dump(obj, indent=2, ensure_ascii=False, sort_keys=False)`.

**`db.py` interface (frozen):**
```
connect(db_path) -> Connection
init_db(conn)
apply_migrations(conn)
find_paper_id_by_alias(conn, alias) -> str | None
file_exists(conn, file_hash) -> bool
upsert_paper(conn, record, record_path)
insert_aliases(conn, paper_id, aliases)
insert_file(conn, paper_id, file_record)
log_processing_run(conn, file_hash, paper_id, stage, status, message)
get_status_counts(conn) -> dict
resolve_id(conn, id_or_alias) -> str | None   # accepts p_..., arxiv:..., doi:..., hash:...
list_papers(conn, *, needs_review=False) -> list[dict]
```

No business logic inside `db.py`. SQL only.

**`resolve_id` behavior:**
```
If input starts with "p_" → treat as paper_id, verify existence.
Else → look up in aliases table (input is a full alias string like "arxiv:2401.12345").
Accept bare input without prefix as hash if it matches /^[a-f0-9]{16}$/.
```

**Identity resolution in ingest (frozen):**
```
1. If file_hash already in files table → skip (exact duplicate).
2. Build candidate aliases from DOI, arXiv ID.
3. For each non-hash alias: find_paper_id_by_alias.
   If any matches → reuse that paper_id (alias_duplicate), attach new file.
4. Otherwise → paper_id = f"p_{hash16}", create new paper record.
```

**Reuse case (alias match):**
```
1. Load existing JSON record.
2. Append new FileRecord to files[] (unless file_hash already present).
3. Merge aliases (union, no duplicates).
4. Do not touch metadata fields.
5. duplicate_status = "alias_duplicate".
6. Write JSON atomically. Update DB.
```

**`rebuild-index` behavior (frozen):**
```
1. If db exists → copy to db/library.backup-YYYYMMDD-HHMMSS.db.
2. Open fresh connection. Apply migrations.
3. Clear papers, aliases, files tables (processing_runs cleared too).
4. Read every records/*.json.
5. For each: validate schema_version == 1 (skip with warning if not).
6. Insert paper row, alias rows, file rows in single transaction.
7. Print: records loaded, records skipped, JSON errors encountered.
```

**Gate**
```
paperlib ingest --no-ai
paperlib ingest --no-ai   # second run
```
Second run creates zero new rows and zero new JSON files. Then:
```
rm {root}/db/library.db
paperlib rebuild-index
paperlib status
```
Counts match pre-deletion state. All JSON records reload cleanly.

---

### Phase 6 — AI Metadata and Summary

**Deliverables**
- `ai/client.py` — thin Anthropic wrapper
- `ai/prompts.py` — prompt template + version constant
- `pipeline/summarise.py` — text truncation, API call, defensive JSON parse, validation, record update
- `pipeline/ingest.py` — wire step 11 (summarise) into state machine

**Key design notes**

**`ai/client.py` interface:**
```
def call_anthropic(prompt: str, *, model: str, max_tokens: int,
                   temperature: float, timeout_s: int = 60) -> str
```
Returns raw text content of first text block. Raises `AIError` on any failure (network, auth, timeout, non-200). Never swallows errors silently — caller handles.

**Prompt contract (in `prompts.py`):**
```
SUMMARY_PROMPT_VERSION = "v1"

Prompt must instruct the model to:
- Return a single JSON object only. No markdown fences. No prose before or after.
- Use null for unknown fields. Do not fabricate.
- Include all required keys (see below) even if values are null or empty arrays.
- For authors: return a JSON array of strings, first author first.
- Keep one_sentence ≤ 30 words, short ≤ 80 words, technical ≤ 300 words.
```

**Required keys in model output (frozen):**
```
title (str|null)
authors (list[str]|null)
journal (str|null)
one_sentence (str|null)
short (str|null)
technical (str|null)
key_contributions (list[str])
methods (list[str])
limitations (list[str])
physics (dict with keys: field, materials, devices, measurements, main_theory)
tags (list[str])
```

**Input truncation:** cleaned text truncated to first 40000 characters. No chunking.

**Defensive JSON parsing (frozen — apply despite strict prompt):**
```python
def strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text
```

**Summary key validation (frozen):**
```
REQUIRED_SUMMARY_KEYS = {
    "status", "locked", "source_file_hash",
    "one_sentence", "short", "technical",
    "key_contributions", "methods", "limitations",
    "physics", "tags"
}
REQUIRED_PHYSICS_KEYS = {
    "field", "materials", "devices", "measurements", "main_theory"
}
```
After construction in `summarise.py`, assert both sets are subsets of the actual keys. If not → set `summary.status = "failed"`, append warning, still write record.

**Metadata update from AI (frozen):**
```
For each of title, authors, journal:
  if model returned non-null AND field.locked is False:
    field.value = model_value
    field.source = "ai"
    field.confidence = 0.70  # fixed default for AI-sourced
    field.updated_at = now()
  else: leave field unchanged
```

Year: do not overwrite from AI. Year comes from arXiv ID or keyword heuristic only. This is deliberate — AI-guessed years are unreliable.

**`--no-ai` behavior:**
```
- Do not require ANTHROPIC_API_KEY at startup.
- Set summary.status = "skipped", summary.source_file_hash = None.
- Do not modify title, authors, journal beyond what non-AI extraction found.
```

**AI failure behavior:**
```
AIError caught in summarise.py:
- summary.status = "failed"
- append warning to summary (e.g., list of warnings, or a notes field — pick one)
- log the error message (not the prompt, not the key)
- continue to step 12 (write JSON)
- record is still valid and reingestable
```

**Gate**

`paperlib ingest --no-ai` runs without `ANTHROPIC_API_KEY`. API failure (simulated by bad key) does not stop batch; record written with `summary.status="failed"`. On success: structured fields populated, `title`/`authors`/`journal` filled where previously null. `summary.locked=true` records untouched on rerun. `title.locked=true` records keep their value.

---

### Phase 7 — CLI Completion

**Deliverables**
- `pipeline/ingest.py` — final `IngestReport` dataclass return
- `cli.py` — `status`, `show`, `list [--needs-review]` commands

**Key design notes**

**`IngestReport` (returned by `ingest_library`):**
```
discovered: int
processed: int
skipped_existing: int
failed: int
records_written: int
summaries_generated: int
summaries_failed: int
summaries_skipped: int
warnings: list[str]
```

Deep pipeline functions return data. Printing is only in `cli.py`. This keeps pipeline testable without capturing stdout.

**`status` output format:**
```
papers:          <n>
files:           <n>
extraction ok:   <n>
extraction partial: <n>
extraction failed:  <n>
needs review:    <n>
summary pending: <n>
summary failed:  <n>
```
If DB missing: print `"No database found. Run paperlib ingest or paperlib rebuild-index."` and exit nonzero.

**`show` behavior:**
```
1. Parse input:
   - starts with "p_" → paper_id
   - contains ":" → alias (arxiv:..., doi:..., hash:...)
   - 16 hex chars → treat as hash alias
2. resolve_id() → paper_id or None
3. If None → print error, exit nonzero.
4. Read record_path from papers table.
5. Print JSON to stdout (indented).
```

**`list` output columns:**
```
paper_id | year | first_author | title (trunc 60) | review_status
```
Missing title → `<no title>`. Missing first_author → `<unknown>`. `--needs-review` filters `review_status="needs_review"`.

**Dry-run invariant (re-verified here):**
`ingest --dry-run` performs:
- discover, validate only
- no text files written
- no PDFs moved
- no JSON written
- no DB writes
- no AI calls

**Gate (benchmark corpus test)**

Use ~15 PDFs: mix of arXiv, journal, equation-heavy, one duplicate copy, one encrypted, one broken, one scanned-only.

Run sequence:
```
paperlib validate-config
paperlib ingest --dry-run
paperlib ingest --limit 5 --no-ai
paperlib status
paperlib list --needs-review
paperlib show <paper_id>
paperlib show arxiv:<id>
paperlib rebuild-index
paperlib ingest --no-ai        # full run
paperlib ingest --no-ai        # idempotency check
paperlib ingest                # with AI
```

Pass criteria: no crash on broken/encrypted PDFs; exact duplicate skipped; alias resolution works; second full ingest creates zero new records; dry-run causes zero filesystem changes; AI failure on one record does not affect others.

---

## Part C — Implementation Order

Code in this exact order. Do not skip ahead.

```
1.  pyproject.toml, .gitignore, README stub
2.  config.example.toml, .env.example
3.  Empty package tree with __init__.py files
4.  models/status.py
5.  models/metadata.py, file.py, identity.py, record.py
6.  config.py
7.  logging_config.py
8.  store/fs.py: ensure_runtime_dirs, sha256_file
9.  cli.py with validate-config only         ─┐ Phase 1 gate
10. pipeline/discover.py
11. pipeline/validate.py
12. cli.py: ingest --dry-run                 ─┐ Phase 2 gate
13. pipeline/extract.py
14. pipeline/clean.py
15. store/fs.py: atomic_write_text, move-to-failed
16. cli.py: ingest --limit N (no JSON/DB)    ─┐ Phase 3 gate
17. models/identity.py: normalize_doi, normalize_arxiv_id
18. pipeline/metadata.py: regex, year heuristic
19. store/fs.py: sanitize_component, canonical_pdf_relative_path
20. (unit tests for 17–19)                   ─┐ Phase 4 gate
21. store/json_store.py
22. store/db.py
23. store/migrations.py
24. cli.py: rebuild-index
25. pipeline/ingest.py (non-AI end-to-end)   ─┐ Phase 5 gate
26. ai/client.py
27. ai/prompts.py
28. pipeline/summarise.py
29. pipeline/ingest.py: wire step 11         ─┐ Phase 6 gate
30. cli.py: status, show, list
31. Full benchmark corpus run                ─┐ Phase 7 gate
32. docs/*.md fill-in
```

---

## Part D — Test Plan

Minimum tests. Write each test **when the corresponding module is written**, not at the end.

**`test_clean.py`**
- Ligature replacement (fi, fl, ff, ffi, ffl)
- Control character removal (keep `\n`, `\t`)
- Line-ending normalization
- Excessive newline/space collapse
- Idempotence: `clean_text(clean_text(x)) == clean_text(x)`

**`test_metadata.py`**
- DOI detection: standard, with URL prefix, with `doi:` prefix
- DOI normalization: case, URL strip, trailing punctuation
- arXiv detection: modern ID, old-style ID, with/without version
- arXiv normalization: version stripping
- Year from arXiv: `2401` → 2024, `9908` → 1999, `2601` → 2026 (current+0)
- Year from keyword heuristic: positive case, negative case (reference year not picked up)

**`test_identity_resolution.py`**
- New file → `paper_id = p_{hash16}`, correct aliases built
- DOI present → DOI alias added, lowercase, stripped
- arXiv present → arXiv alias added, no version

**`test_fs.py`**
- `sanitize_component("Müller")` → deterministic ASCII result (document actual output)
- `sanitize_component("van den Berg")` → `"van_den_berg"`
- `sanitize_component("Smith Jr.")` → `"smith_jr"`
- `sanitize_component("")` → `""`
- `sanitize_component("a" * 100)` → length ≤ 40
- `sanitize_component("Cao/Chen:Wang")` → `"cao_chen_wang"`
- `atomic_write_text` produces final file only, no `.tmp` leftover on success
- `sha256_file` matches `hashlib.sha256(file_bytes).hexdigest()` on a small fixture

**`test_json_store.py`**
- Round-trip: write → read → equal
- Invalid `schema_version` raises
- Atomic write: after write, file is complete and parseable

**`test_db.py`**
- `init_db` creates all tables
- Insert paper → retrievable
- Insert aliases → `find_paper_id_by_alias` returns correct ID
- `file_exists` true after insert, false before
- `resolve_id` accepts `p_`, `arxiv:`, `doi:`, bare 16-hex-hash

**`test_ingest_idempotency.py`**
- Given a fixture PDF in a temp inbox:
  - First ingest creates 1 paper, 1 file, N aliases
  - Second ingest creates 0 new rows
  - Text file, JSON file, and moved PDF exist exactly once

**`test_discover.py`, `test_validate.py`, `test_extract.py`**
- Minimal smoke tests on fixture PDFs. Acceptable to mock `pdfplumber` for unit isolation.

**Fixture strategy:** generate minimal synthetic PDFs programmatically where possible (using `reportlab` in test-only dependencies, or check in small safe PDFs). Do not commit copyrighted papers.

---

## Part E — Documentation To Write (Phase 7 end)

Keep brief. Write these only after implementation is complete so they describe what was built, not what was planned.

**`README.md`** — Overview, install, quick start, minimal CLI reference, link to docs.

**`docs/architecture.md`** — Ingest state machine diagram, source-of-truth rule, `paper_id` rule, canonical filename rule.

**`docs/schema.md`** — Full JSON schema, full SQLite schema, all enums, metadata source values.

**`docs/config.md`** — Every `config.toml` key, `.env` variables, `validate-config` behavior (including the "root must exist" rule), `--no-ai` and AI key interaction.

**`docs/operations.md`** — How to ingest, dry-run, rebuild index, manually edit JSON, set `locked=true` on a field, handle files in `failed/`.

**`docs/limitations.md`** — No OCR, equation extraction is poor, journal frequently null for arXiv, metadata never fabricated, no external lookups in v1.

**`docs/roadmap.md`** — OCR, Crossref/arXiv API lookup, fuzzy duplicate detection, simple search, RAG, UI.

---

## Part F — Implementation Rules

1. One module at a time. Write the module, write its tests, run the phase gate, move on.
2. Do not revisit Part A. It is frozen.
3. When a real implementation obstacle appears, choose the simplest viable fix and document it in `docs/architecture.md`. Do not redesign.
4. Never log API keys, prompts, or full extracted text at INFO level. DEBUG only, and disabled by default.
5. All filesystem writes (text, JSON, DB) must be atomic or transactional. No exceptions.
6. Unknown metadata is `null`. Never a fabricated value. Never a placeholder string like `"unknown"` in metadata `value` fields (only in filename components).
7. Phase gates are mandatory. Do not accumulate unverified work across phases.

Start Phase 1.