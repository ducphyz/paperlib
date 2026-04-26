# Schema

`paperlib` v1 stores canonical paper records as JSON and maintains a rebuildable
SQLite index over those records.

## JSON Records

Records are written to:

```text
records/{paper_id}.json
```

Top-level keys:

- `schema_version`: v1 schema marker.
- `paper_id`: stable internal ID, `p_{hash16}` from the first file.
- `identity`: DOI, arXiv ID, and aliases.
- `files`: ingested file records, including canonical PDF path, text path, size,
  and extraction metrics.
- `metadata`: `title`, `authors`, `year`, and `journal` as metadata field
  objects.
- `summary`: optional AI summary structure.
- `status`: metadata, summary, duplicate, and review status values.
- `review`: manual review notes and lock flag.
- `timestamps`: record creation and update timestamps.

Metadata fields use:

```json
{"value": null, "source": null, "confidence": null, "locked": false, "updated_at": null}
```

Unknown metadata values are `null`. Filename placeholders such as
`unknown_year` are not stored as metadata values.

## SQLite Index

SQLite lives at:

```text
db/library.db
```

Tables:

- `papers`: one row per paper record.
- `aliases`: lookup aliases such as `hash:<hash16>`, `arxiv:<id>`, and
  `doi:<doi>`.
- `files`: one row per ingested PDF file.
- `processing_runs`: ingest and validation run logs.
- `schema_migrations`: applied SQLite schema versions.

SQLite is rebuildable from JSON:

```bash
paperlib rebuild-index
```

## Status Values

Extraction status:

- `pending`
- `ok`
- `partial`
- `failed`

Extraction quality:

- `good`
- `low_text`
- `scanned`
- `equation_heavy`
- `unknown`

Metadata status:

- `pending`
- `ok`
- `partial`
- `needs_review`
- `failed`

Summary status:

- `pending`
- `generated`
- `failed`
- `skipped`

Duplicate status:

- `unique`
- `exact_duplicate`
- `alias_duplicate`

Review status:

- `needs_review`
- `reviewed`

Metadata sources:

- `pdf_embedded_meta`
- `pdf_text`
- `filename`
- `ai`
- `user`
