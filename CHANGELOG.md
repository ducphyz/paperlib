# Changelog

All notable changes to PaperLib are documented here.

This project follows the spirit of Keep a Changelog.

## [v1.1] - 2026-04-28

Feature milestone v1.1 is released as package version 0.1.1.

### Added

- Embedded PDF metadata extraction for non-AI ingest, including title, authors,
  year, and creation-date derived year where available.
- Conservative filename heuristics for missing metadata when embedded PDF
  metadata is incomplete.
- Human-readable `handle_id` values such as `smith_2014`, distinct from the
  immutable internal `paper_id`.
- `rebuild-index` handle backfill for existing JSON records, with `--dry-run`
  and `--no-backfill` controls.
- `handle_id` support in `paperlib list`, `paperlib show`, and ID resolution.
- `mark-reviewed` to mark a full record reviewed and locked.
- Interactive `review` command for editing metadata, locking reviewed fields,
  and optionally locking the whole record.
- Multi-provider AI dispatch with model prefixes for Anthropic, OpenAI,
  OpenRouter, and generic OpenAI-compatible endpoints.
- Optional `paperlib[openai]` extra for OpenAI-compatible providers.
- CLI help text, global `--config`, and `paperlib --version`.

### Changed

- Canonical PDF filenames now use `{author}_{year}_{hash8}.pdf` under the
  existing `papers/{year}/` directory layout.
- Logging is initialized by CLI commands so ingest and maintenance activity is
  written to `logs/ingest.log`.
- SQLite schema migrates to version 2 to add the nullable unique `handle_id`
  column and index. JSON `schema_version` remains `1`.
- `paperlib list` shows `handle_id` by default and can sort with
  `--sort handle`.
- `paperlib show <handle_id>` resolves the same record as its `paper_id`.

### Fixed

- `--no-ai` ingest no longer leaves common embedded title, author, and year
  metadata blank when the PDF or filename contains usable values.
- Locked metadata fields are preserved on re-ingest and after AI output.
- Fully locked reviewed records are skipped on re-ingest instead of being
  silently overwritten.
- AI failures remain non-fatal across providers; records are still written with
  failed summary status.

### Migration Notes

- Run `paperlib rebuild-index` once after upgrading. This applies SQLite schema
  version 2, backfills missing `handle_id` values into JSON and SQLite, and
  rebuilds aliases.
- Re-running `paperlib rebuild-index` is safe and idempotent.
- Existing Anthropic configs without a model prefix remain supported.
- Install `paperlib[openai]` before using `openai:`, `openrouter:`, or
  `openai-compat:` models.
