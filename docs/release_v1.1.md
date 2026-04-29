# PaperLib v1.1 Release Notes

Feature milestone v1.1 is released as package version 0.1.1.

## Scope

PaperLib v1.1 rounds out the local-first paper library workflow. It adds
embedded PDF metadata extraction for non-AI ingest, conservative filename
heuristics, author-first canonical PDF filenames, active logging, stable
human-readable `handle_id` values, handle backfill through `rebuild-index`,
handle-aware lookup/list/show behavior, review locking, `mark-reviewed`,
interactive `review`, multi-provider AI dispatch, CLI help/version polish, and
updated release documentation and config examples.

## Source Code Inventory

Line counts are physical lines, generated from `src/paperlib/**/*.py` at release
documentation time.

| File | Lines | Responsibility / functionality |
| --- | ---: | --- |
| `src/paperlib/__about__.py` | 6 | Defines package title, version, and short CLI description for version/help output. |
| `src/paperlib/__init__.py` | 0 | Package marker for `paperlib`. |
| `src/paperlib/ai/__init__.py` | 0 | Package marker for AI helpers. |
| `src/paperlib/ai/client.py` | 205 | AI provider parsing and dispatch for Anthropic, OpenAI, OpenRouter, and OpenAI-compatible endpoints. |
| `src/paperlib/ai/prompts.py` | 62 | Builds the structured summary prompt and defines the summary prompt version. |
| `src/paperlib/cli.py` | 634 | Click CLI entry point for config validation, ingest, status, list, show, rebuild-index, review, and mark-reviewed. |
| `src/paperlib/config.py` | 209 | Loads TOML and `.env` configuration, resolves library paths, and validates AI provider settings. |
| `src/paperlib/handle.py` | 99 | Generates stable human-readable `handle_id` values with collision handling. |
| `src/paperlib/logging_config.py` | 30 | Configures file and console logging for CLI commands. |
| `src/paperlib/models/__init__.py` | 0 | Package marker for data models. |
| `src/paperlib/models/file.py` | 76 | Defines file and extraction dataclasses stored in paper records. |
| `src/paperlib/models/identity.py` | 68 | Defines DOI, arXiv, and alias identity helpers and normalization. |
| `src/paperlib/models/metadata.py` | 32 | Defines `MetadataField` values with source, confidence, lock state, and timestamps. |
| `src/paperlib/models/record.py` | 139 | Defines canonical `PaperRecord` serialization, defaults, and backwards-compatible loading. |
| `src/paperlib/models/status.py` | 33 | Centralizes status and source string constants used across the pipeline. |
| `src/paperlib/pipeline/__init__.py` | 0 | Package marker for ingest pipeline stages. |
| `src/paperlib/pipeline/clean.py` | 28 | Normalizes extracted text before metadata detection and summarization. |
| `src/paperlib/pipeline/discover.py` | 44 | Discovers inbox PDFs and computes file identity, size, and modified-time metadata. |
| `src/paperlib/pipeline/extract.py` | 199 | Extracts PDF text and embedded PDF metadata with `pdfplumber`. |
| `src/paperlib/pipeline/ingest.py` | 520 | Orchestrates discovery, validation, extraction, metadata, AI, file moves, JSON writes, and SQLite updates. |
| `src/paperlib/pipeline/metadata.py` | 450 | Detects DOI, arXiv, year, embedded metadata, and filename-derived metadata fields. |
| `src/paperlib/pipeline/summarise.py` | 306 | Builds AI prompts, parses model JSON, applies AI output, and records non-fatal summary failures. |
| `src/paperlib/pipeline/validate.py` | 58 | Validates PDF readability and text presence before ingest. |
| `src/paperlib/review.py` | 301 | Implements testable interactive metadata review, field locking, identity edits, and review status updates. |
| `src/paperlib/store/__init__.py` | 0 | Package marker for storage helpers. |
| `src/paperlib/store/db.py` | 606 | Manages SQLite indexing, ID resolution, aliases, ingest transactions, and rebuild-index behavior. |
| `src/paperlib/store/fs.py` | 214 | Provides hashing, sanitization, canonical paths, atomic text writes, and file moves. |
| `src/paperlib/store/json_store.py` | 49 | Reads and atomically writes canonical JSON paper records. |
| `src/paperlib/store/migrations.py` | 109 | Creates and migrates the SQLite schema, including schema version 2 handle support. |

Total source Python lines: 4,477.

## Test Inventory

Line counts are physical lines, generated from `tests/test_*.py` at release
documentation time.

| Test file | Lines | Coverage focus |
| --- | ---: | --- |
| `tests/test_ai_client.py` | 395 | AI provider parsing, Anthropic wrapper behavior, OpenAI-compatible dispatch, missing dependency handling, and optional live smoke gates. |
| `tests/test_clean.py` | 34 | Text cleaning and whitespace normalization. |
| `tests/test_cli.py` | 1103 | CLI command behavior, help/version output, config forms, ingest/list/show/rebuild-index, review, and mark-reviewed flows. |
| `tests/test_cli_phase2.py` | 153 | CLI logging activation and log file behavior. |
| `tests/test_config.py` | 172 | TOML config loading, AI provider defaults, base URL validation, and API key environment handling. |
| `tests/test_db.py` | 716 | SQLite schema, migrations, aliases, ID resolution, list sorting/filtering, and rebuild-index backfill behavior. |
| `tests/test_discover.py` | 43 | Inbox PDF discovery and file hash metadata. |
| `tests/test_docs.py` | 82 | Changelog, README, config example validation, and CLI documentation smoke checks. |
| `tests/test_extract.py` | 171 | PDF text extraction and embedded metadata extraction behavior. |
| `tests/test_fs.py` | 254 | Filesystem sanitization, canonical paths, hashing, atomic writes, and file moves. |
| `tests/test_handle.py` | 45 | `handle_id` generation, fallbacks, and collision suffixes. |
| `tests/test_identity_resolution.py` | 57 | Paper identity alias construction and duplicate-resolution identifiers. |
| `tests/test_ingest_ai.py` | 377 | Ingest behavior with AI enabled, disabled, skipped, failed, and duplicate cases. |
| `tests/test_ingest_idempotency.py` | 841 | End-to-end ingest idempotency, handle stability, locked-field preservation, and duplicate handling. |
| `tests/test_json_store.py` | 71 | JSON record read/write round trips, schema validation, and atomic write cleanup. |
| `tests/test_metadata.py` | 271 | DOI, arXiv, year, embedded metadata, and filename metadata heuristics. |
| `tests/test_prompts.py` | 73 | Summary prompt content and prompt-version stability. |
| `tests/test_record.py` | 23 | `PaperRecord` compatibility for missing and present `handle_id` fields. |
| `tests/test_review.py` | 398 | Interactive review helper behavior, CLI review integration, locking, cancellation, and alias refresh. |
| `tests/test_summarise.py` | 630 | AI summary JSON parsing, normalization, application to records, locking, and failure handling. |
| `tests/test_validate.py` | 102 | PDF validation success, failure, and sampled text behavior. |

Total test Python lines: 6,011.

## Release Validation Commands

Use the module entry point in a source checkout:

```bash
PYTHONPATH=src python -m pytest
PYTHONPATH=src python -m paperlib.cli --version
PYTHONPATH=src python -m paperlib.cli --help
PYTHONPATH=src python -m paperlib.cli validate-config --config config.example.toml
```

When the package is installed and the console script is available:

```bash
paperlib --version
paperlib --help
paperlib validate-config --config config.example.toml
```

## Known Limitations

- No OCR for scanned PDFs.
- No first-page text title/author heuristic yet.
- Embedded PDF metadata may be missing, stripped, stale, or incorrect.
- No vector database, embeddings, or RAG yet.
- No BibTeX export yet.
