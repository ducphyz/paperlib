# Architecture

`paperlib` v1 is a local CLI pipeline for turning PDFs into a structured paper
library. The canonical data is stored as JSON records. SQLite is a rebuildable
index over those records.

## High-Level Architecture

- `paperlib.cli` defines the command line interface and handles user output.
- `paperlib.config` loads `config.toml` and `.env`, resolves library paths, and
  returns an `AppConfig`.
- `paperlib.pipeline` contains the ingest stages: discovery, validation,
  extraction, cleaning, metadata detection, summarisation, and orchestration.
- `paperlib.store` contains filesystem helpers, atomic JSON persistence, SQLite
  indexing, and schema migrations.
- `paperlib.ai` contains the Anthropic client wrapper and summary prompt
  construction.
- `paperlib.models` contains dataclasses for records, files, identity,
  metadata fields, and status constants.

Pipeline modules return data. CLI printing stays in `cli.py`.

## Runtime Layout

Given a configured `library.root`, v1 uses this layout:

```text
{library_root}/
├── inbox/
├── papers/{year}/{year}_{first_author}_{hash8}.pdf
├── records/{paper_id}.json
├── text/{hash16}.txt
├── db/library.db
├── logs/
├── failed/
└── duplicates/
```

`inbox/` is the input queue. Valid ingested PDFs are moved under `papers/`.
Extracted text goes under `text/`. Canonical records go under `records/`.
SQLite lives at `db/library.db`. Invalid PDFs are moved to `failed/`.

## Source of Truth

JSON records are canonical.

SQLite is an index. It can be deleted and rebuilt from `records/*.json` with:

```bash
paperlib rebuild-index
```

On conflict, JSON is the authoritative representation.

## Identity

The internal `paper_id` is assigned from the first file used to create the
record:

```text
p_{sha256_of_first_file[:16]}
```

The `paper_id` is stable and never changes, even if later aliases or duplicate
files are attached to the same record.

## Aliases

Records carry aliases for lookup and duplicate detection:

```text
hash:<hash16>
arxiv:<id>
doi:<doi>
```

Every file contributes a `hash:<hash16>` alias. DOI and arXiv aliases are added
when detected. During ingest, non-hash aliases can resolve a new file to an
existing paper record.

## Ingest State Machine

For each PDF selected from `inbox/`:

1. Discover PDF path, size, modified time, and SHA-256 hash.
2. Deduplicate exact files by checking the SQLite `files` table.
3. Validate readability and sampled text presence.
4. Extract full text with `pdfplumber`.
5. Clean extracted text.
6. Identify DOI, arXiv ID, aliases, and target `paper_id`.
7. Decide canonical PDF filename.
8. Move the PDF to `papers/{year}/`.
9. Write cleaned text to `text/{hash16}.txt`.
10. Build non-AI metadata fields.
11. Optionally summarise with AI.
12. Write the JSON record.
13. Update SQLite in a transaction.
14. Log the processing run.

Dry runs stop at discovery and validation and do not write files, move PDFs,
update SQLite, or call AI.

## Canonical Filenames

Canonical PDF filenames use:

```text
{year}_{first_author}_{hash8}.pdf
```

The containing directory is:

```text
papers/{year}/
```

If the year is unknown, `unknown_year` is used. If the first author is unknown,
`unknown_author` is used. These placeholders are filename components only; they
are not metadata values.

## AI Rules

AI summarisation is optional. `paperlib ingest --no-ai` avoids AI completely.
When AI is enabled, ingest attempts to use Anthropic for metadata and summary
generation.

AI failures do not stop ingest. The record is still written with a failed
summary status where appropriate.

AI never overwrites locked metadata or locked summary fields. AI also never
overwrites `metadata.year`; year comes only from deterministic non-AI heuristics.

## Atomicity

Text and JSON writes use a temporary file, `fsync`, and atomic rename.

SQLite updates use transactions for ingest success and rebuild-index indexing.
This keeps the JSON source of truth and the SQLite index recoverable after
interrupted writes.

## Design Limitations

v1 intentionally does not implement:

- OCR
- external metadata APIs
- RAG
- embeddings
