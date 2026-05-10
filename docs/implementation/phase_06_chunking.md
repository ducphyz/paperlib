# Phase 6 — Chunking

Full spec: [`v1_3_plan.md § Phase 6`](../../v1_3_plan.md)

## Goal

Split full text into overlapping chunks carrying source quality metadata. Produce the
`Chunk` objects that Phase 7 stores in the `chunks` table.

## Prerequisites

- Phase 1 — `MarkdownInfo` on `FileRecord`; physics fields in `_default_summary()`.
- Phase 5 — `chunks` table must exist before Phase 7 can store chunk output. (Chunking
  logic itself does not touch the DB, but is tested end-to-end with Phase 7.)
- Phase 15 — `SearchConfig` for chunking params; `ExtractionConfig.markdown_fallback_partial`.

## Files to create

| File | Action |
|---|---|
| `src/paperlib/search/__init__.py` | Create (empty) |
| `src/paperlib/search/models.py` | Create — `Chunk` dataclass (and eventually `SearchResult`, `ScoreBreakdown`) |
| `src/paperlib/search/chunking.py` | Create — `chunk_document()` |

---

## Implementation

### `Chunk` dataclass — `search/models.py`

```python
from dataclasses import dataclass

@dataclass
class Chunk:
    chunk_id: str           # "{paper_id}_c{N:04d}" — deterministic
    paper_id: str
    file_hash: str
    source_type: str        # "markdown_api" | "local_txt"
    location_confidence: str  # "high" | "medium" | "low"
    section_title: str | None
    page_start: int | None
    page_end: int | None
    chunk_order: int
    text: str
    text_hash: str          # sha256 of text
    created_at: str
```

### `chunk_document` — `search/chunking.py`

```python
def chunk_document(record: PaperRecord, config) -> list[Chunk]:
```

#### Step 1 — File selection

`PaperRecord.files` may contain multiple `FileRecord`s. Select exactly one to chunk:

1. If `record.summary.get("source_file_hash")` is set, and a `FileRecord` with that
   hash exists, and that file has `extraction.status == "ok"` **and**
   `extraction.word_count > 0`: use it.
2. Otherwise sort candidates by:
   - Extraction status priority: `"ok"` > `"partial"` > `"failed"` / `"pending"`. Any
     non-ok status sorts below ok.
   - Then `word_count` descending. Files with `word_count = 0` sort below any file with
     words, regardless of quality label.
   - Then extraction quality: `"good"` > `"equation_heavy"` > `"low_text"` >
     `"unknown"` > `"scanned"`. (`scanned` is last because scanned PDFs commonly produce
     zero or near-zero extractable text even when `word_count > 0`;  `equation_heavy`
     beats `low_text` because it has usable prose around equations.)
   - Take the first result.

Only one file is chunked per paper. Record the selected `file_hash` on every `Chunk`.

**Why `source_file_hash` target with `word_count = 0` falls through:** a designated
source file that was never OCR'd (or failed extraction) should not be preferred over a
file with actual text, even if it is the "canonical" source.

#### Step 2 — Source priority

Given the selected file record:

| Condition | `source_type` | `location_confidence` | Notes |
|---|---|---|---|
| `file_record.markdown.status == "validated"` | `"markdown_api"` | `"high"` | Chunk from `.md` |
| `file_record.markdown.status == "partial"` and `config.extraction.markdown_fallback_partial == True` | `"markdown_api"` | `"medium"` | Chunk from `.md` with warning |
| No markdown, or `status == "failed"`, or partial not allowed | `"local_txt"` | `"low"` | Chunk from `.txt`; `page_start`/`page_end` are `None` |

#### Step 3 — Chunking rules

**Parameters:**
- Target chunk size: 250 words.
- Overlap: 60 words.

**For Markdown source:**
1. Strip `<!-- page: N -->` markers from stored chunk text (but track them for
   `page_start`/`page_end` assignment).
2. Split on `##` section boundaries first. Each section is chunked independently.
3. Within a section, split by word count (250 words target, 60 word overlap). Include
   the section heading at the start of each chunk within that section.
4. Assign `section_title` from the `##` heading.
5. Assign `page_start` and `page_end` by tracking which `<!-- page: N -->` markers fall
   within the character range of the chunk.

**For `.txt` source:**
1. No section parsing (no `##` headings).
2. Split by word count (250 words target, 60 word overlap).
3. `section_title = None`, `page_start = None`, `page_end = None`.

#### Step 4 — Chunk ID

```python
chunk_id = f"{paper_id}_c{chunk_order:04d}"
```

Deterministic from `paper_id` and `chunk_order`. For unchanged source text and chunking
config, a rebuild produces identical `chunk_id` values. This is what allows embeddings
to survive a `rebuild-search-index` run when `source_hash` matches.

#### Step 5 — `text_hash`

```python
import hashlib
text_hash = hashlib.sha256(chunk.text.encode()).hexdigest()
```

---

## Edge cases

- Record with zero files: `chunk_document` returns `[]`.
- All files have `word_count = 0`: select the first by quality sort; the chunker will
  produce zero or one chunk from empty text.
- Markdown file listed in `MarkdownInfo.markdown_path` does not exist on disk: fall back
  to `.txt` source with `location_confidence = "low"`.
- `.txt` file also missing: return `[]`.
- Page markers not monotonic in source (validated Markdown can be `"partial"`): still
  extract what markers are present; assign `page_start`/`page_end` best-effort.
- Very short sections (< 250 words): emit as a single chunk without overlap.

---

## Tests required

`tests/test_chunking.py` (new):
- Chunk count and section headers from a fixture `.md` file.
- Page markers correctly assigned to `page_start`/`page_end`.
- `source_type = "markdown_api"` and `location_confidence = "high"` for validated
  Markdown.
- `source_type = "local_txt"` and `location_confidence = "low"` for `.txt` fallback.
- `page_start` and `page_end` are `None` for `.txt` source.
- File selection: equation-heavy file with `word_count > 0` beats a scanned/empty file.
- File selection: `source_file_hash` target with `word_count = 0` falls through to
  fallback sort.
- Chunk IDs are deterministic: same source text and config → same `chunk_id` values on
  two calls.
- Empty file list → `[]`.

---

## Acceptance criteria

- [ ] `Chunk` dataclass exists in `search/models.py` with all specified fields.
- [ ] `chunk_document(record, config) -> list[Chunk]` exists in `search/chunking.py`.
- [ ] File selection follows the priority order (source_file_hash first, then
  status/word_count/quality sort).
- [ ] `source_file_hash` target with `word_count = 0` falls through to sort.
- [ ] Source priority: validated markdown → high; partial (if allowed) → medium; txt → low.
- [ ] Chunk IDs are deterministic.
- [ ] Page markers stripped from stored text; used only for page range assignment.
- [ ] `.txt` source produces `page_start = None`, `page_end = None`.
