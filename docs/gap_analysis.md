# PaperLib Functional Gap Analysis

## Summary table

| Gap | Command | Risk | Key entanglements |
| --- | --- | --- | --- |
| GAP-1 | `paperlib re-summarise` | Medium | Reuses `summarise_record`, but must reconstruct ingest's cleaned-text and source-file context from JSON/file storage and keep JSON and SQLite in sync. |
| GAP-2 | `paperlib validate-library` | Medium | Cross-checks JSON, SQLite, PDFs, and text files; must remain read-only and avoid accidentally creating a missing SQLite database. |
| GAP-3 | `paperlib list` alignment | Low | Formatting is inline in `cli.py`; existing CLI tests assert exact unpadded output and will need updates. |
| GAP-4 | Crossref/arXiv lookup | High | Adds network metadata into ingest before summarisation; must respect locks, avoid new dependency drift, and define timeout/error behavior. |
| GAP-5 | `paperlib export --bibtex` | Medium | Needs reusable ID resolution/all-record loading and careful plain-text BibTeX escaping without adding dependencies. |
| GAP-6 | `paperlib search` | Medium | Depends on SQLite title/authors columns, JSON summary storage, and preferably a reusable list-output formatter from GAP-3. |

## GAP-1: re-summarise

### Insertion points

- `src/paperlib/cli.py`: add a new Click command on `main`, likely near `ingest` or `status`, with optional `id_or_alias`, `--no-ai`, `--limit`, `--config`, and probably `--debug` if matching ingest maintenance commands.
- `src/paperlib/cli.py`: factor or duplicate the existing ID resolution pattern from `show`, `mark_reviewed`, and `review`: load config, check `config.paths.db`, connect with `db.connect`, call `db.resolve_id`, call `db.get_record_path`, resolve relative paths against `config.library.root`, then `read_record`.
- `src/paperlib/store/db.py`: add a query helper for eligible records, for example records whose `papers.summary_status` is `failed` or `skipped`, returning at least `paper_id` and `record_path`. Current `list_papers` does not expose `record_path` or `summary_status`.
- `src/paperlib/pipeline/ingest.py`: the current summarisation call is inside `_ingest_pdf`, after `file_record` is appended, the PDF is moved, and cleaned text is written, and before JSON write/SQLite update. Re-summarise should not call `_ingest_pdf`; it needs its own command-level loop because `_ingest_pdf` expects an inbox `DiscoveredPDF`, validation/extraction results, file moves, and duplicate handling.
- `src/paperlib/pipeline/summarise.py`: reuse `summarise_record(record, cleaned_text=..., source_file_hash=..., ai_config=config.ai, no_ai=...)` as-is. No changes are required by the gap statement.
- `src/paperlib/store/json_store.py`: use `write_record_atomic` for the JSON write.
- `src/paperlib/store/db.py`: after JSON write, use `db.update_record_index` or `db.upsert_paper`. `upsert_paper` is enough for `papers.summary_status`, title/authors/journal, and timestamps, but `update_record_index` matches the review command's JSON-then-SQLite pattern and refreshes aliases defensively.

### Difficulties

- Ingest calls `summarise_record` with a fully populated `PaperRecord`: identity is set, metadata is populated/merged, `record.files` contains the current `FileRecord`, `summary.locked` has been checked, and `cleaned_text` is an in-memory value from `clean_text(extraction.raw_text)`.
- Re-summarise must reconstruct that context from storage. The best source file appears to be `record.files` plus each file's `text_path` and `file_hash`. `FileRecord.text_path` is stored as a root-relative path like `text/<hash16>.txt`; ingest also writes the same file at `config.paths.text / f"{pdf.hash16}.txt"`.
- If a text file is missing, the current code has no recovery path. The command cannot call `summarise_record` without `cleaned_text`; it must explicitly decide whether to leave the record unchanged with a warning or mark the summary failed and update JSON/SQLite. Because the gap requires SQLite updates "on success or failure", marking summary failed is the more consistent command behavior, but this is new command behavior rather than something already defined.
- Source file selection is non-obvious for records with multiple files. Ingest summarises the file currently being ingested. Re-summarise should probably prefer `record.summary["source_file_hash"]` when present and matching a file, otherwise the newest or first `record.files` entry. The current code does not define this policy.
- `--no-ai` can reuse `summarise_record` because that function marks the summary as `skipped` and does not call AI. This means `re-summarise --no-ai` on a failed record would change it to skipped; that matches the existing summarise function but may surprise users.
- `summary.locked` should be checked before reading text or calling AI. `summarise_record` also checks it, but the command should skip locked summaries silently to satisfy the gap and avoid unnecessary filesystem work.
- AI failures are already non-fatal inside `summarise_record`; it returns `(record, False, error_message)` after setting summary/status to `failed`.
- Ingest snapshots locked metadata before summarise and restores it afterwards via `_locked_metadata` and `_restore_locked_metadata`. The real `apply_ai_output_to_record` checks each metadata field's `locked` flag, but ingest's restoration is an extra guard and tests deliberately mock `summarise_record` to violate that invariant. Re-summarise should either reuse equivalent snapshot/restore logic or extract it into a shared helper before implementation.

### Entanglements

- Depends on `PaperRecord.files` and `FileRecord.text_path` from `models/file.py`.
- Depends on `db.resolve_id` for ID/alias behavior and `db.get_record_path` for JSON location.
- Depends on `summarise_record` side effects: it may update summary status, metadata title/authors/journal, metadata status, and timestamps.
- Entangled with GAP-2 because validate-library would detect the missing text files that make re-summarise impossible.
- Entangled with GAP-3/GAP-6 only if a shared command-result or list formatter is introduced, but no direct dependency exists.

### Risk: Medium — justification

The AI call itself is reusable, but selecting the correct stored text file, handling missing text, preserving locked fields, and updating JSON before SQLite without ingest's surrounding pipeline make this more than a small CLI wrapper.

## GAP-2: validate-library

### Insertion points

- `src/paperlib/cli.py`: add a new `validate-library` Click command with `--config`; optionally add `--debug` if it logs.
- New helper module is advisable, for example `src/paperlib/store/validate_library.py` or `src/paperlib/pipeline/validate_library.py`, to keep the CLI from accumulating filesystem/SQL logic. The current `pipeline/validate.py` is PDF validation only, so overloading it would be confusing.
- `src/paperlib/store/db.py`: add read helpers for all paper rows and all file rows. Existing helpers are point lookups (`get_record_path`, `find_paper_id_by_file_hash`) or list rows for CLI display (`list_papers`), and none return complete `record_path`, `canonical_path`, `text_path`, and `file_hash` sets.
- `src/paperlib/store/json_store.py`: use `read_record` or `read_record_dict` to parse records from `config.paths.records.glob("*.json")`.
- `src/paperlib/models/file.py`: JSON file entries are `record.files`, each with `file_hash`, `original_filename`, `canonical_path`, `text_path`, `size_bytes`, `added_at`, and nested `extraction`.

### Difficulties

- JSON canonical paths are stored in `PaperRecord.files[*].canonical_path`, and text paths are stored in `PaperRecord.files[*].text_path`; both are relative to `config.library.root` in current ingest output.
- SQLite stores `record_path` in `papers` and `canonical_path`, `text_path`, and `file_hash` in `files`, but `db.py` has no list-all helper for validation. Direct SQL is possible, but adding read-only helpers would make tests cleaner.
- Detecting JSON without SQLite entry can be done by comparing JSON `paper_id`s to `papers.paper_id` rows. Bad JSON or unsupported schema should be reported separately, not treated as a missing DB row.
- Detecting SQLite entries without JSON needs `papers.record_path`; if the stored path is relative, resolve it under `config.library.root` as `show` and `review` do.
- Detecting missing PDFs/text files should use JSON canonical paths because JSON is the canonical source. SQLite can be cross-checked separately to detect index drift.
- Orphan PDFs in `papers/` can be detected by scanning `config.paths.papers.rglob("*.pdf")`, converting to root-relative POSIX-like paths, and comparing with every JSON `files[*].canonical_path`. SQLite also stores `files.canonical_path`, but JSON should be authoritative.
- Recoverable inconsistencies: missing SQLite rows and stale SQLite rows are recoverable with `paperlib rebuild-index`; SQLite files rows that differ from JSON are also recoverable by rebuild. Non-recoverable or manual inconsistencies: missing canonical PDF, missing text file if no PDF re-extraction command exists, bad JSON, and orphaned PDFs whose identity is unknown.
- A read-only validation command can still create state accidentally if it uses `db.connect` on a missing DB path, because `sqlite3.connect` creates the database file and `connect` creates the parent directory. The implementation should check `config.paths.db.exists()` first or use SQLite URI read-only mode.
- There is low corruption risk if the command never calls `init_db`, migrations, `write_record_atomic`, or mutating DB helpers. This should be a hard implementation constraint.

### Entanglements

- Strongly entangled with `db.rebuild_index_from_records`, because several findings should recommend rebuild-index as remediation.
- Entangled with GAP-1 because missing text files block re-summarise.
- Entangled with `store/fs.py` only for path normalization; no existing helper converts arbitrary root-relative paths, so that may need to be created carefully.
- Entangled with docs/tests because validation output needs a stable severity/category format for test assertions.

### Risk: Medium — justification

The command is conceptually read-only, but it crosses every persistence layer and must avoid the existing `db.connect` behavior that can create a missing database.

## GAP-3: list column alignment

### Insertion points

- `src/paperlib/cli.py`, `list_command`: list formatting is currently inline. It builds `columns`, prints `" | ".join(columns)`, then builds `values` and prints `" | ".join(values)`.
- `src/paperlib/cli.py`, helpers `_first_author` and `_truncate_title`: `_truncate_title` already truncates values longer than 60 characters to 57 characters plus `...`, matching the suggested visible title width of 60 rather than the prompt's "title 57" wording.
- A new helper in `cli.py` such as a private row formatter would make GAP-6 search output reusable. There is no shared list formatting function today.

### Difficulties

- Existing tests assert exact unpadded pipe-separated substrings in `tests/test_cli.py`, especially `test_list_prints_missing_title_unknown_author_and_truncated_title`, `test_list_no_handle_hides_handle_column`, `test_list_needs_review_filters_rows`, `test_list_invalid_authors_json_prints_unknown`, and `test_list_after_cli_ingest_prints_ingested_row`.
- `--no-handle` changes the column count by removing `handle_id`, so the formatter must support two schemas or dynamically omit that column while preserving widths for the remaining columns.
- Suggested widths need a precise interpretation. The current `_truncate_title` returns 60 visible characters for long titles, while the prompt says "title 57 (truncated with ... beyond 57 chars)", which could mean 57 total including ellipsis or 57 plus ellipsis. Tests should lock down the intended behavior before implementation.
- Missing values currently render as `<none>`, `<unknown>`, and `<no title>`. Padding must preserve those strings.
- Pipe separators can remain if desired, but fixed-width padding means tests should avoid brittle full-line matching unless the full table contract is intended.

### Entanglements

- GAP-6 should reuse the same formatting helper so search output matches list output.
- GAP-5 might also want a common record-loading helper, but not the list formatter.
- No storage or data-model changes are required.

### Risk: Low — justification

The change is localized to CLI presentation, but exact-output tests will need coordinated updates.

## GAP-4: Crossref and arXiv lookup

### Insertion points

- New module `src/paperlib/pipeline/lookup.py` for Crossref/arXiv API calls, response parsing, and applying lookup results to `MetadataField`s.
- `src/paperlib/pipeline/ingest.py`, `_ingest_pdf`: insert after local metadata extraction has populated or merged `record.identity` and `record.metadata`, and before `_build_file_record`. This is after `_new_record(...)` or `_load_existing_record(...)` plus `_merge_unlocked_metadata(...)`, and before the canonical filename is computed and before `summarise_record`.
- `src/paperlib/models/status.py`: add source constants for `crossref` and `arxiv_api` if following the existing status-constant convention. Current sources are `pdf_embedded_meta`, `pdf_text`, `filename`, `ai`, and `user`; the requested source strings do not exist.
- `tests/test_metadata.py` or new `tests/test_lookup.py`: add parser/unit tests for Crossref JSON, arXiv Atom XML, locked-field behavior, and non-fatal failures.
- `tests/test_ingest_idempotency.py` or `tests/test_ingest_ai.py`: add ingest integration tests proving lookup is called after local metadata and before summarise, does not overwrite locked fields, and failures do not abort ingest.

### Difficulties

- The lookup should run only when `identity.doi` or `identity.arxiv_id` is set and title is null, per the gap. If title is present but authors or journal are missing, the prompt does not authorize a lookup; this should be explicit in implementation tests.
- The existing metadata pipeline uses `MetadataField(value, source, confidence, locked, updated_at)`, so lookup results can populate the same structure with confidence `0.9` and `updated_at=now`.
- Locked-field preservation does not apply automatically unless lookup mutates a `PaperRecord` through explicit checks. `_merge_unlocked_metadata` protects existing records only for incoming local metadata, and `apply_ai_output_to_record` protects AI writes. A lookup module must check `record.metadata[field].locked` before setting title/authors/year/journal.
- Ingest currently has no direct HTTP calls and no timeout/retry pattern. AI calls are hidden behind provider SDKs in `ai/client.py`; there is no generic HTTP helper. The lookup module must define timeouts and failure mapping without pulling in a dependency unless project policy changes.
- `urllib.request` and `json` from the standard library are enough for Crossref; `xml.etree.ElementTree` is enough for arXiv Atom XML if namespace handling is implemented. arXiv entries use Atom namespaced elements such as feed/entry/title, entry/author/name, entry/published, and optional arXiv namespaced journal reference fields.
- Crossref DOI path values need URL encoding. A DOI contains `/` and may contain punctuation that should not be used raw in a URL path. Use URL quoting with no unsafe path characters for Crossref. For arXiv IDs, old-style IDs contain `/`, so arXiv URL quoting may need to preserve `/` or encode carefully according to the API endpoint's accepted form.
- Crossref polite pool access requires a descriptive `User-Agent`; config has no application/contact field, so a static user agent based on package name/version is likely needed.
- Non-fatal failure means network errors, HTTP non-200 responses, JSON/XML parse errors, and missing fields should leave metadata unchanged and ingest should continue. Logging should avoid noisy stack traces for expected lookup misses unless debug logging is enabled.
- External API lookup may alter metadata before canonical filename creation. That is desirable for better filenames, but it means tests for canonical filenames may need updates if lookup is enabled by default.
- There is no config flag for enabling/disabling lookup. The gap says add lookup behavior but does not specify config. Default-on network behavior during ingest may surprise local-first users and tests unless tests monkeypatch the lookup call. This cannot be answered from current source and should be decided before implementation.

### Entanglements

- Strongly entangled with ingest's lock-preservation rules and metadata status calculation.
- Entangled with config if lookup needs opt-in/offline mode, contact email, timeout, or user-agent configuration.
- Entangled with GAP-1 indirectly: re-summarise should not perform metadata lookup because it must reuse `summarise_record` only, but summaries may depend on lookup-improved identifiers/metadata after future ingest.
- Entangled with tests that assume no network during ingest; the implementation should isolate network calls for monkeypatching.

### Risk: High — justification

This introduces network IO into the core ingest path, new source constants, URL/XML/JSON parsing, timeout policy, and lock-preserving metadata mutation.

## GAP-5: export --bibtex

### Insertion points

- `src/paperlib/cli.py`: add an `export` command or command group with a `--bibtex` mode, depending on desired CLI shape. The prompt says "paperlib export --bibtex", so a single command with a `--bibtex` flag is the smallest compatible surface.
- `src/paperlib/cli.py`: reuse the show command's ID resolution flow for one or more ID/alias arguments: `db.resolve_id`, `db.get_record_path`, resolve relative path, then `read_record`.
- `src/paperlib/store/db.py`: for exporting all records through SQLite, add a helper returning all `paper_id`/`record_path` pairs in a stable order. Current `list_papers` does not return `record_path`.
- Alternative all-record path: scan `config.paths.records.glob("*.json")` and read JSON directly because JSON is canonical. This avoids stale SQLite omissions but cannot use SQLite ordering and will not surface DB alias issues.
- New formatter module is advisable, for example `src/paperlib/export.py`, for BibTeX key generation, field escaping, author formatting, and record-to-entry conversion. Keeping this out of `cli.py` will make tests much cleaner.

### Difficulties

- `metadata.authors` is a `MetadataField`; its `.value` is expected to be a list of strings when present. This is enforced by summarise normalization and review author parsing, and DB stores it as JSON in `authors_json`.
- BibTeX author formatting should probably join existing strings with ` and ` rather than trying to rewrite names into `Last, First`. The current pipeline stores mixed formats from embedded metadata, filename heuristics, user input, and AI; reliable name inversion is not available.
- BibTeX escaping is required for at least backslash, braces, percent, dollar, ampersand, hash, underscore, caret, and tilde. Existing metadata can plausibly include underscores from filenames, ampersands in titles, braces from pasted titles, Unicode names, and LaTeX-like text.
- Entry type policy is clear but needs field policy: DOI records become `@article`, arXiv-only records become `@misc` with `eprint` and `archivePrefix = {arXiv}`, records with neither become `@misc` with `note = {paper_id}`.
- Cite key uniqueness should use `handle_id` when present because it is user-facing and unique in SQLite, with fallback to `paper_id` because it is immutable and unique. If exporting directly from JSON, duplicate or missing handle IDs are possible in stale records; fallback and de-duplication should be explicit.
- `--output FILE` should probably use an atomic text write if overwriting inside the library, but `store.fs.atomic_write_text` writes UTF-8 text and is suitable for general paths as long as parent creation behavior is acceptable.
- Records with missing titles/authors/year need valid minimal BibTeX rather than errors. The exact fallback strings should be specified in tests.

### Entanglements

- Depends on `db.resolve_id` and `read_record` for targeted export.
- Shares all-record loading concerns with GAP-1 and GAP-6; a small private CLI helper could reduce duplication.
- Does not require schema changes.
- Not dependent on GAP-3, unless export wants to share row selection/order with list.

### Risk: Medium — justification

No new dependencies or persistence changes are needed, but correct escaping, stable cite keys, and stale-index behavior require careful choices.

## GAP-6: search

### Insertion points

- `src/paperlib/cli.py`: add `search QUERY --field title|authors|summary|all --config`, likely near `list` because output should match list.
- `src/paperlib/store/db.py`: add a search helper using parameterized SQL against `papers.title` and `papers.authors_json`. Current available columns are `title`, `authors_json`, `year`, `review_status`, `handle_id`, `paper_id`, DOI/arXiv/status fields, and `record_path`.
- `src/paperlib/cli.py`: extract the current list formatter from `list_command` so search can reuse the exact same output. Today the formatter is inline.
- For summary search, records must be loaded from JSON because SQLite does not store summary text fields. Text files under `text/` contain cleaned paper text, not summary content. The prompt's "optional text file scan for summary content" conflicts with the current storage model; if the goal is summary search, scan JSON `record.summary` fields, not text files.

### Difficulties

- SQLite can efficiently do simple `LIKE` over `papers.title` and `papers.authors_json`, but `authors_json` is serialized JSON text. It will work for substring search but not structured author matching.
- `summary` fields are only in JSON: `one_sentence`, `short`, `technical`, lists like `key_contributions`, `methods`, `limitations`, and nested `physics` values. There are no summary columns in SQLite.
- Scanning all JSON summaries is O(number of records) and likely fine for a local paper library at hundreds or low thousands of records. Scanning all extracted text files is much more expensive and semantically searches paper full text, not summary. The project has no documented scale threshold, so this cannot be answered from source.
- Multi-word query behavior is not defined by existing code. A direct `LIKE '%query%'` gives phrase semantics. AND/OR token semantics would need explicit design and tests.
- SQL injection must be avoided with parameterized queries. Existing DB code uses parameters for user input in point lookups; `list_papers` uses f-strings only for internal predicates/order clauses selected from known values. Search should not interpolate the query into SQL.
- Literal `%` and `_` in user queries have wildcard meaning in SQL LIKE. The implementation should decide whether to treat them as wildcards or escape them for literal keyword search.
- Output "matching paperlib list" is easiest after GAP-3 creates a shared formatter; otherwise search will duplicate current inline list formatting and then need later rework.

### Entanglements

- Strongly entangled with GAP-3 because search output should match list output.
- Entangled with GAP-5 and GAP-1 through common record loading if summary search or targeted commands read JSON by record path.
- Entangled with GAP-2 if validation reveals stale SQLite rows; search via SQLite can return records whose JSON no longer exists unless it resolves paths defensively.

### Risk: Medium — justification

Title/author SQL search is straightforward, but summary search is not indexed and the prompt's text-file wording conflicts with current storage semantics.

## Cross-gap dependencies

- Implement GAP-3 before GAP-6 to avoid building search output against the old unpadded list format and then rewriting it.
- Implement a small shared ID-resolution/record-loading helper before GAP-1 and GAP-5 if both are planned; `show`, `mark-reviewed`, and `review` already duplicate this flow.
- Implement GAP-2 before GAP-1 if possible, because validate-library will expose missing text files that make re-summarise fail.
- GAP-4 should be implemented after the re-summarise design is clear, but it should not be called by re-summarise; lookup belongs in ingest before summarisation.
- GAP-5 can be implemented independently of the other gaps, except for any shared record-loading helper.
- GAP-2 should remain read-only and should not be coupled to repair behavior; repair actions should stay in `rebuild-index` or future explicit commands.

Test suite result: PASS — `PYTHONPATH=src python -m pytest` completed with 275 passed, 2 skipped.
