# Schema

`paperlib` v1 stores canonical paper records as JSON and maintains a rebuildable
SQLite index over selected record fields.

## JSON Schema Overview

Records are written to:

```text
records/{paper_id}.json
```

Top-level keys:

- `schema_version`: integer schema marker. v1 records use `1`.
- `paper_id`: stable internal ID, `p_{sha256_of_first_file[:16]}`.
- `identity`: DOI, arXiv ID, and lookup aliases.
- `files`: ingested PDF files, their canonical paths, text paths, sizes, and
  extraction metrics.
- `metadata`: `title`, `authors`, `year`, and `journal` as `MetadataField`
  objects.
- `summary`: optional structured AI summary state and content.
- `status`: current metadata, summary, duplicate, and review status values.
- `review`: manual review notes and review lock.
- `timestamps`: record creation and update timestamps.

Unknown values are stored as `null`. Filename fallbacks such as `unknown_year`
and `unknown_author` are not metadata values.

## Representative JSON Record

```json
{
  "schema_version": 1,
  "paper_id": "p_abc123def4567890",
  "identity": {
    "doi": "10.1103/physrevlett.123.456",
    "arxiv_id": "2401.12345",
    "aliases": [
      "hash:abc123def4567890",
      "arxiv:2401.12345",
      "doi:10.1103/physrevlett.123.456"
    ]
  },
  "files": [
    {
      "file_hash": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
      "original_filename": "paper.pdf",
      "canonical_path": "papers/2024/2024_unknown_author_abc123de.pdf",
      "text_path": "text/abc123def4567890.txt",
      "size_bytes": 1234567,
      "added_at": "2026-04-27T10:00:00Z",
      "extraction": {
        "status": "ok",
        "engine": "pdfplumber",
        "engine_version": "0.11.9",
        "page_count": 12,
        "char_count": 43210,
        "word_count": 6100,
        "quality": "good",
        "warnings": []
      }
    }
  ],
  "metadata": {
    "title": {
      "value": "Example Paper Title",
      "source": "ai",
      "confidence": 0.7,
      "locked": false,
      "updated_at": "2026-04-27T10:05:00Z"
    },
    "authors": {
      "value": ["A. Smith", "B. Jones"],
      "source": "ai",
      "confidence": 0.7,
      "locked": false,
      "updated_at": "2026-04-27T10:05:00Z"
    },
    "year": {
      "value": 2024,
      "source": "pdf_text",
      "confidence": 0.95,
      "locked": false,
      "updated_at": "2026-04-27T10:00:00Z"
    },
    "journal": {
      "value": null,
      "source": null,
      "confidence": null,
      "locked": false,
      "updated_at": null
    }
  },
  "summary": {
    "status": "generated",
    "source_file_hash": "abc123def4567890abc123def4567890abc123def4567890abc123def4567890",
    "model": "claude-sonnet-4-20250514",
    "prompt_version": "v1",
    "generated_at": "2026-04-27T10:05:00Z",
    "locked": false,
    "one_sentence": "A concise one-sentence summary of the paper.",
    "short": "A short summary of the paper.",
    "technical": "A more technical summary of the paper.",
    "key_contributions": ["Contribution one", "Contribution two"],
    "methods": ["Method one"],
    "limitations": ["Limitation one"],
    "physics": {
      "field": "condensed matter physics",
      "materials": [],
      "devices": [],
      "measurements": [],
      "main_theory": []
    },
    "tags": ["example", "physics"]
  },
  "status": {
    "metadata": "ok",
    "summary": "generated",
    "duplicate": "unique",
    "review": "needs_review"
  },
  "review": {
    "notes": "",
    "locked": false
  },
  "timestamps": {
    "created_at": "2026-04-27T10:00:00Z",
    "updated_at": "2026-04-27T10:05:00Z"
  }
}
```

## MetadataField

Each metadata entry has this structure:

```json
{
  "value": null,
  "source": null,
  "confidence": null,
  "locked": false,
  "updated_at": null
}
```

- `value`: the field value, or `null` when unknown.
- `source`: metadata source, or `null` when unknown.
- `confidence`: numeric confidence, or `null` when unknown.
- `locked`: when `true`, automated ingest and AI updates must not overwrite the
  field.
- `updated_at`: ISO 8601 timestamp for the last update, or `null`.

Implemented metadata fields are `title`, `authors`, `year`, and `journal`.

## Summary

The summary object contains:

- `status`
- `source_file_hash`
- `model`
- `prompt_version`
- `generated_at`
- `locked`
- `one_sentence`
- `short`
- `technical`
- `key_contributions`
- `methods`
- `limitations`
- `physics`
- `tags`

When `summary.locked` is `true`, automated ingest and AI updates must not
overwrite the summary.

## Physics

The `summary.physics` object contains:

- `field`
- `materials`
- `devices`
- `measurements`
- `main_theory`

Unknown scalar values are `null`. Unknown list values are empty arrays.

## Status Enums

`ExtractionStatus`:

- `pending`
- `ok`
- `partial`
- `failed`

`ExtractionQuality`:

- `good`
- `low_text`
- `scanned`
- `equation_heavy`
- `unknown`

`MetadataStatus`:

- `pending`
- `ok`
- `partial`
- `needs_review`
- `failed`

`SummaryStatus`:

- `pending`
- `generated`
- `failed`
- `skipped`

`DuplicateStatus`:

- `unique`
- `exact_duplicate`
- `alias_duplicate`

`ReviewStatus`:

- `needs_review`
- `reviewed`

`MetadataSource`:

- `pdf_embedded_meta`
- `pdf_text`
- `filename`
- `ai`
- `user`

## SQLite Schema

SQLite lives at:

```text
db/library.db
```

Full v1 schema:

```sql
CREATE TABLE IF NOT EXISTS papers (
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

CREATE TABLE IF NOT EXISTS aliases (
    alias       TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    alias_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE IF NOT EXISTS files (
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

CREATE TABLE IF NOT EXISTS processing_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash    TEXT,
    paper_id     TEXT,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    message      TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_doi
    ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_arxiv
    ON papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_papers_year
    ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_review
    ON papers(review_status);
CREATE INDEX IF NOT EXISTS idx_files_paper
    ON files(paper_id);
CREATE INDEX IF NOT EXISTS idx_aliases_paper
    ON aliases(paper_id);
```

## JSON and SQLite Relationship

JSON records are canonical. SQLite stores an indexed subset of JSON fields for
lookup, listing, status reporting, duplicate detection, and file tracking.

`paperlib rebuild-index` clears and repopulates SQLite from `records/*.json`.
If JSON and SQLite disagree, JSON wins.
