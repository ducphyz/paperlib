# Architecture

`paperlib` v1 is a local CLI pipeline for ingesting PDFs into a structured paper
library. JSON records are the canonical data. SQLite is a rebuildable index over
those records.

## High-Level Architecture

- `paperlib.cli` defines the command line interface and owns all user-facing
  output.
- `paperlib.config` loads `config.toml` and `.env`, resolves configured paths
  relative to `library.root`, and returns an `AppConfig`.
- `paperlib.pipeline` contains the ingest stages: discovery, validation,
  extraction, cleaning, metadata detection, summarisation, and orchestration.
- `paperlib.pipeline.lookup` calls Crossref and arXiv APIs to fill metadata
  fields during ingest.
- `paperlib.utils` provides shared utilities (`utc_now`, `field_exists`,
  `metadata_status`, `resolve_library_path`) used across pipeline, store, and
  CLI modules.
- `paperlib.export` formats records as BibTeX.
- `paperlib.store` contains filesystem helpers, atomic text and JSON writes,
  SQLite indexing, and schema migrations.
- `paperlib.store.validate_library` checks consistency across JSON, SQLite,
  PDFs, and text files.
- `paperlib.ai` contains the Anthropic client wrapper and summary prompt
  construction.
- `paperlib.models` contains dataclasses for records, files, identity,
  metadata fields, and status constants.

Pipeline modules return data. CLI printing stays in `paperlib.cli`.

## Runtime Layout

Given a configured `library.root`, v1 uses this layout:

```text
{library_root}/
├── inbox/
├── papers/{year}/{first_author}_{year}_{hash8}.pdf
├── records/{paper_id}.json
├── text/{hash16}.txt
├── db/library.db
├── logs/
├── failed/
├── deleted/
└── duplicates/
```

- `inbox/` contains PDFs waiting for ingest.
- `papers/{year}/...` contains ingested PDFs with canonical filenames.
- `records/{paper_id}.json` contains canonical JSON records.
- `text/{hash16}.txt` contains cleaned extracted text.
- `db/library.db` contains the SQLite index.
- `logs/` is reserved for runtime logs.
- `failed/` receives invalid or unreadable PDFs.
- `deleted/` receives PDFs removed by `paperlib delete`.
- `duplicates/` is part of the runtime layout for duplicate handling.

## Source of Truth

JSON records are canonical. SQLite can be deleted and rebuilt from
`records/*.json`:

```bash
paperlib rebuild-index
```

On conflict, the JSON record is authoritative.

## Paper IDs

The internal `paper_id` is assigned from the first file used to create a record:

```text
p_{sha256_of_first_file[:16]}
```

The `paper_id` is stable and never changes, even if later files or aliases are
attached to the same record.

## Aliases

Records carry aliases for lookup and duplicate detection:

```text
hash:<hash16>
arxiv:<id>
doi:<doi>
```

Each ingested file contributes a `hash:<hash16>` alias. DOI and arXiv aliases
are added when detected. During ingest, non-hash aliases can resolve a new file
to an existing paper record.

## Ingest State Machine

For each PDF selected from `inbox/`:

1. Discover the PDF path, size, modified time, and SHA-256 hash.
2. Deduplicate exact files by checking the SQLite `files` table.
3. Validate readability and sampled text presence.
4. Extract full text with `pdfplumber`.
5. Clean extracted text.
6. Extract non-AI metadata (DOI, arXiv ID, year) and build metadata fields.
7. Identify aliases and target `paper_id`.
8. Optionally call Crossref or arXiv to fill title, authors, year, journal
   when `[lookup] enabled = true`.
9. Assign `handle_id` (generated from enriched author/year after lookup).
10. Decide the canonical filename.
11. Move the PDF to `papers/{year}/`.
12. Write cleaned text to `text/{hash16}.txt`.
13. Optionally summarise with AI.
14. Write the JSON record.
15. Update SQLite in a transaction.
16. Log the processing run.

Dry runs stop at discovery and validation. They do not move PDFs, write text,
write JSON, update SQLite, or call AI.

## Canonical Filenames

Canonical PDF filenames use:

```text
{first_author}_{year}_{hash8}.pdf
```

The containing directory is:

```text
papers/{year}/
```

If the year is unknown, `unknown_year` is used. If the first author is unknown,
`unknown_author` is used. These fallbacks are filename components only; unknown
metadata values remain `null`.

## AI Rules

AI summarisation is optional. `paperlib ingest --no-ai` avoids AI entirely.
Without `--no-ai`, ingest attempts AI only when `ai.enabled = true`.

AI failures do not stop ingest. The record is still written, and the summary
status is marked failed for the affected record.

AI never overwrites locked metadata or locked summaries. AI also never
overwrites `metadata.year`; year is set only by deterministic non-AI detection.

## Atomicity

Text and JSON writes use a temporary file, `fsync`, and atomic rename.

SQLite updates use transactions for successful ingest and index rebuilds. This
keeps JSON canonical and makes the SQLite index recoverable.

## Limitations by Design

v1 does not implement:

- OCR
- RAG
- embeddings
