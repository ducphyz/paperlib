# paperlib v1.2 source inventory

Feature milestone v1.2, package version 1.2.0.

## Scope

paperlib v1.2 adds Crossref/arXiv metadata lookup, keyword search, BibTeX
export, re-summarise, validate-library, delete, and internal refactoring to
eliminate duplicated utilities across pipeline, store, and CLI.

## Source

| File | Lines | Responsibility |
| --- | ---: | --- |
| `src/paperlib/__about__.py` | 6 | Package version and description. |
| `src/paperlib/__init__.py` | 0 | Package marker. |
| `src/paperlib/ai/__init__.py` | 0 | Package marker. |
| `src/paperlib/ai/client.py` | 203 | AI provider dispatch: Anthropic, OpenAI, OpenRouter, OpenAI-compatible. |
| `src/paperlib/ai/prompts.py` | 62 | Summary prompt builder and prompt version constant. |
| `src/paperlib/cli.py` | 1226 | All CLI commands, display formatting, shared ID resolution, config/DB lifecycle. |
| `src/paperlib/config.py` | 228 | TOML and `.env` loading, path resolution, AI and lookup config. |
| `src/paperlib/export.py` | 84 | BibTeX formatter: cite-key rules, entry types, field escaping. |
| `src/paperlib/handle.py` | 99 | `handle_id` generation with collision suffix handling. |
| `src/paperlib/logging_config.py` | 30 | File and console logging setup. |
| `src/paperlib/models/__init__.py` | 0 | Package marker. |
| `src/paperlib/models/file.py` | 76 | `FileRecord` and `ExtractionInfo` dataclasses. |
| `src/paperlib/models/identity.py` | 68 | DOI/arXiv alias construction and normalization. |
| `src/paperlib/models/metadata.py` | 32 | `MetadataField`: source, confidence, lock, timestamps. |
| `src/paperlib/models/record.py` | 139 | `PaperRecord` serialization, defaults, backwards-compatible loading. |
| `src/paperlib/models/status.py` | 35 | Status and source string constants. |
| `src/paperlib/pipeline/__init__.py` | 0 | Package marker. |
| `src/paperlib/pipeline/clean.py` | 28 | Text normalization before metadata detection and summarization. |
| `src/paperlib/pipeline/discover.py` | 44 | Inbox PDF discovery and file identity metadata. |
| `src/paperlib/pipeline/extract.py` | 199 | PDF text and embedded metadata extraction via pdfplumber. |
| `src/paperlib/pipeline/ingest.py` | 497 | Ingest orchestration: validation, extraction, metadata, lookup, AI, file moves, JSON, SQLite. |
| `src/paperlib/pipeline/lookup.py` | 182 | Crossref and arXiv API lookup to fill metadata during ingest. |
| `src/paperlib/pipeline/metadata.py` | 445 | DOI, arXiv, year, embedded PDF metadata, and filename heuristics. |
| `src/paperlib/pipeline/summarise.py` | 291 | AI prompt, JSON parsing and validation, output application, failure handling. |
| `src/paperlib/pipeline/validate.py` | 58 | PDF readability and text presence validation. |
| `src/paperlib/review.py` | 301 | Interactive metadata review, field locking, identity edits, review status. |
| `src/paperlib/store/__init__.py` | 0 | Package marker. |
| `src/paperlib/store/db.py` | 704 | SQLite indexing, ID resolution, aliases, ingest transactions, search, rebuild-index. |
| `src/paperlib/store/fs.py` | 220 | Hashing, canonical paths, atomic writes, file moves to failed/deleted/duplicates. |
| `src/paperlib/store/json_store.py` | 49 | Atomic JSON record read/write. |
| `src/paperlib/store/migrations.py` | 109 | SQLite schema creation and migration. |
| `src/paperlib/store/validate_library.py` | 116 | Cross-layer integrity checks: JSON, SQLite, PDFs, text files. |
| `src/paperlib/utils.py` | 39 | Shared utilities: `utc_now`, `field_exists`, `metadata_status`, `resolve_library_path`. |

Total: 5,570 lines.

## Tests

| File | Lines | Coverage |
| --- | ---: | --- |
| `tests/test_ai_client.py` | 369 | AI provider parsing, dispatch, missing-dependency handling. |
| `tests/test_clean.py` | 34 | Text cleaning and whitespace normalization. |
| `tests/test_cli.py` | 1694 | All CLI commands. |
| `tests/test_config.py` | 239 | Config loading, AI/lookup config, path resolution, API key handling. |
| `tests/test_db.py` | 893 | Schema, migrations, aliases, ID resolution, search, rebuild-index. |
| `tests/test_discover.py` | 43 | Inbox discovery and file hash metadata. |
| `tests/test_docs.py` | 86 | Changelog, README, config example smoke checks. |
| `tests/test_export.py` | 186 | BibTeX formatting, cite-key rules, entry types, CLI export. |
| `tests/test_extract.py` | 171 | PDF text and embedded metadata extraction. |
| `tests/test_fs.py` | 284 | Sanitization, canonical paths, hashing, atomic writes, file moves. |
| `tests/test_handle.py` | 45 | `handle_id` generation, fallbacks, collision suffixes. |
| `tests/test_identity_resolution.py` | 57 | Alias construction and duplicate-resolution identifiers. |
| `tests/test_ingest_ai.py` | 335 | Ingest with AI: enabled, disabled, skipped, failed, duplicate detection. |
| `tests/test_ingest_idempotency.py` | 896 | End-to-end idempotency, handle stability, locked fields, duplicates. |
| `tests/test_json_store.py` | 71 | JSON round trips, schema validation, atomic write cleanup. |
| `tests/test_lookup.py` | 268 | Crossref/arXiv lookup, locked fields, fallback, monkeypatched HTTP. |
| `tests/test_metadata.py` | 271 | DOI, arXiv, year, embedded metadata, filename heuristics. |
| `tests/test_prompts.py` | 73 | Prompt content and version stability. |
| `tests/test_record.py` | 23 | `PaperRecord` field compatibility. |
| `tests/test_resummary.py` | 551 | Re-summarise: single, batch, locked, failure. |
| `tests/test_review.py` | 362 | Interactive review, locking, cancellation, alias refresh. |
| `tests/test_summarise.py` | 630 | AI JSON parsing, normalization, locking, failure. |
| `tests/test_validate.py` | 102 | PDF validation success and failure. |
| `tests/test_validate_library.py` | 232 | Integrity checks: missing DB, bad JSON, missing PDF/text, orphan PDFs. |

Total: 7,915 lines.

## Known limitations

- No OCR.
- No first-page title/author heuristic; uses embedded PDF metadata and filename heuristics.
- Embedded PDF metadata may be missing, stale, or wrong.
- No embeddings, vector search, or RAG.
