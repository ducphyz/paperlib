# PaperLib v1.1 — Fix Plan

**Status:** Plan only. No code, no prompt text, no migration SQL.
**Audience:** This document will be fed to an implementation agent that turns each phase into concrete code, tests, and migrations.
**Source of truth:** JSON records in `records/` are canonical. SQLite is rebuildable. Both must be kept in sync at write time.

---

## 0. Goals

1. **Fix the `--no-ai` "authors unknown" gap.** When AI is disabled, ingest must still capture title/authors/year via embedded PDF metadata (and filename heuristics) instead of writing `null` everywhere.
2. **Activate logging.** `setup_logging` is defined but never called, so `logs/ingest.log` is always empty.
3. **Introduce `handle_id`** — a stable, human-readable identifier (e.g. `smith_2014`) separate from `paper_id`. Use it in CLI output, `list`, and `show`.
4. **Wire `review.locked` into the ingest pipeline.** The field exists in the schema but is never enforced; locked records can currently be silently overwritten on re-ingest.
5. **Add a `mark-reviewed` / `review` workflow** so users can lock human-verified records.
6. **Expand AI provider support** beyond hard-wired Anthropic to OpenAI, OpenRouter, and any OpenAI-compatible endpoint, selected via a provider prefix in the model string.
7. **CLI polish + docs.** `--help` improvements, `__about__.py` for version reporting, CHANGELOG, README updates.

Nothing in this plan changes the v1 schema in a backwards-incompatible way. `schema_version` stays at `1` for the JSON files (the `handle_id` field is additive and tolerated by `from_dict`); the SQLite schema is bumped via the existing `migrations.py` versioning path.

---

## 1. Scope guardrails (do not violate)

- **No re-ingest required.** All migrations must be runnable against an existing library without re-extracting text or re-calling AI.
- **`paper_id` is immutable.** Never recompute, never rename, never alter the directory layout that derives from it.
- **JSON-first.** Every change writes the JSON record atomically *before* updating SQLite. SQLite is a derived index.
- **Atomic writes.** Use the existing `atomic_write_text` / `write_record_atomic` helpers (random tempfile + fsync + `os.replace`). Do not introduce new write paths.
- **AI failure must never abort a batch.** Existing behavior (catch all exceptions, set `summary.status="failed"`, continue) is preserved across all providers.
- **No new mandatory dependencies.** The `openai` SDK is added as an *optional* extra (`paperlib[openai]`), not a hard requirement.
- **Dry-run stays side-effect-free.** Every new write path must respect `--dry-run`.

---

## 2. Phase overview

| Phase | Title | Priority | Touches | Gate |
|-------|-------|----------|---------|------|
| 1 | Embedded metadata + filename heuristic | **P0** | `pipeline/extract.py`, `pipeline/metadata.py`, `pipeline/ingest.py` | `--no-ai` produces non-null author/title/year |
| 2 | Activate logging | **P0** | `cli.py` | `logs/ingest.log` non-empty after any command |
| 3 | `handle_id` model + DB column | **P1** | `models/record.py`, `store/db.py`, `store/migrations.py`, `pipeline/ingest.py` | New ingests get a `handle_id`; old records readable |
| 4 | `rebuild-index` backfills `handle_id` | **P1** | `cli.py`, `store/json_store.py` | Existing libraries gain `handle_id` without re-ingest |
| 5 | `resolve_id` + `list` + `show` use `handle_id` | **P1** | `store/db.py`, `cli.py` | `paperlib show smith_2014` works |
| 6 | Enforce `review.locked` in ingest + add `mark-reviewed` | **P1** | `pipeline/ingest.py`, `cli.py` | Locked records survive re-ingest unchanged |
| 7 | Interactive `review` command | **P1** | `cli.py` | Users can edit + lock fields from the terminal |
| 8 | **Multi-provider AI (Anthropic / OpenAI / OpenRouter)** | **P1** | `ai/client.py`, `config.py`, `pipeline/summarise.py`, `pyproject.toml` | All three providers verified against live endpoints |
| 9 | CLI help + `__about__.py` | **P2** | `cli.py`, `src/paperlib/__about__.py` | `paperlib --version` works; help text useful |
| 10 | CHANGELOG + README | **P2** | `CHANGELOG.md`, `README.md`, `config.example.toml` | Docs match behavior |

Phases are ordered by dependency, not by P0/P1/P2. Each phase has its own gate; do not start phase N+1 until phase N's gate passes.

---

## 3. Phase 1 — Embedded metadata + filename heuristic (P0)

### Problem
Running `paperlib ingest --no-ai` writes records where `title`, `authors`, and `year` are all `null` and `paper.first_author` is the literal string `None`. Four compounding gaps:

1. `pipeline/extract.py` opens the PDF with `pdfplumber.open(path)` but never reads `pdf.metadata` (the `/Author`, `/Title`, `/CreationDate` dict).
2. `pipeline/metadata.py::build_non_ai_metadata_fields` only populates `year`. There is no plumbing for title or authors.
3. `pipeline/ingest.py::_build_file_record` hardcodes `first_author=None`.
4. `store/fs.py::canonical_pdf_relative_path` emits `{year}_{author}_{hash8}.pdf` — year-first ordering makes filenames hard to scan by author name.

### Fix
- **Extract layer:** Add a small helper that reads `pdf.metadata` and returns a typed dict of `{title, authors, year, creation_date}`, normalizing `/Author` (semicolon/comma-split into a list) and `/Title`. Defensive: skip empty strings, strip whitespace, drop obviously-junk values (e.g., `"untitled"`, single-character titles, encoder names like `"Microsoft Word"`).
- **Metadata layer:** Extend `build_non_ai_metadata_fields` to accept the embedded-metadata dict plus the original filename. Populate `title`, `authors`, `year` `MetadataField`s with `source="pdf_embedded"` or `source="filename"` and `confidence` values reflecting reliability (embedded ≈ 0.6, filename heuristic ≈ 0.3).
- **Filename heuristic:** Add a parser for common patterns (`Author2014_Title.pdf`, `2014 - Author - Title.pdf`, `arXiv-2401.12345v2.pdf`). Used **only** when embedded metadata is missing the field. Defer text-based extraction (first-page parsing) to v1.2.
- **Ingest layer:** In `_build_file_record`, derive `first_author` from the populated `authors` field (first element if present, else `None`).
- **Canonical filename order:** Change `store/fs.py::canonical_pdf_relative_path` to emit `{author}_{year}_{hash8}.pdf` (author first). Directory layout `papers/{year}/` is unchanged. Fallbacks: `unknown_author` and `unknown_year` as before.
  - Old: `papers/2014/2014_smith_abcd1234.pdf`
  - New: `papers/2014/smith_2014_abcd1234.pdf`
  - Update `implement_plan.md` A.2 Core Rules to reflect the new format.
- **AI override:** When AI runs and returns a value with higher confidence, the existing precedence rules in `apply_ai_output_to_record` already win — verify this still holds (test).

### Gate
1. New unit tests cover: PDF with full embedded metadata, PDF with partial metadata, PDF with no metadata + parseable filename, PDF with no metadata + unparseable filename.
2. End-to-end: `paperlib ingest --no-ai --limit 3` against the sanity corpus produces records with non-null `title`, `authors`, `year` for at least 2 of 3 PDFs (the third may legitimately have no metadata and an opaque filename).
3. `paper.first_author` is never the literal string `"None"`.
4. Canonical paths follow the new `{author}_{year}_{hash8}.pdf` pattern — confirmed via `test_fs.py`.
5. AI-on path still produces the same outputs as before (regression test on `apply_ai_output_to_record`).

---

## 4. Phase 2 — Activate logging (P0)

### Problem
`logging_config.py::setup_logging` is well-formed but never invoked. `logs/ingest.log` is always empty. CLI output is the only record of what happened.

### Fix
- Call `setup_logging(config.paths.logs, debug=...)` once at the top of every CLI subcommand that does work (`ingest`, `rebuild-index`, future `review`, `mark-reviewed`). `validate-config`, `status`, `show`, `list` should also log at INFO level so user activity is auditable.
- Add a `--debug` global flag (or per-subcommand if global is hard given the current Click structure) that flips the level to DEBUG.
- Replace any `print()` calls in pipeline code with `logger.info` / `logger.warning` / `logger.error` — but keep CLI-level user-facing prints (the `[1/12]` progress line, summary tables) using `click.echo` so the human display is unchanged.

### Gate
1. After any ingest run, `logs/ingest.log` is non-empty and contains structured timestamped entries.
2. `--debug` produces additional DEBUG-level output (e.g. per-page extraction details).
3. No regressions in CLI stdout — the human-facing display is identical.

---

## 5. Phase 3 — `handle_id` model + DB column (P1)

### Problem
`paper_id` (`p_<sha256_first16>`) is opaque. Users need a memorable handle for `show`, `list`, and `mark-reviewed`.

### Fix
- **Model change:** Add `handle_id: str | None` to `PaperRecord`. Keep `paper_id` as the primary key. `to_dict()` emits `handle_id` after `paper_id`. `from_dict()` tolerates its absence (returns `None`).
- **Generation rule:**
  - Base = `{first_author_lastname_lowercase}_{year}` after sanitization (ASCII only, alphanumerics + underscore, max 40 chars).
  - If first_author is unknown → fallback to `untitled_{year}` or `paper_{short_hash}`.
  - On collision, append `_b`, `_c`, ... (skip `_a` so the first one stays clean).
  - Never reuse a freed handle.
- **Generator location:** New module `src/paperlib/handle.py` with `generate_handle_id(record, existing_handles: set[str]) -> str`. Pure function; no I/O.
- **DB schema bump:** Add `handle_id TEXT UNIQUE` column to the `papers` table via `store/migrations.py` (migration version 2). Index it. Existing rows get `NULL` initially; phase 4 backfills them.
- **Ingest integration:** During `_build_file_record`, after the record is otherwise complete, query the DB for existing `handle_id`s, generate the handle, set it on the record, and pass through to JSON write + DB insert.

### Gate
1. New ingests produce JSON records with a non-null `handle_id`.
2. `migrations.py` reports version `2` after running.
3. Pre-v1.1 records still load without errors (their `handle_id` is `None` until backfilled).
4. Collision logic verified by unit test (two papers with same author/year get distinct handles).

---

## 6. Phase 4 — `rebuild-index` backfills `handle_id` (P1)

### Problem
Existing libraries don't have `handle_id` after the schema change. Forcing re-ingest is unacceptable (per scope guardrails).

### Fix
- Extend the existing `rebuild-index` command:
  1. Pass 1: scan all JSON records, collect any that already have `handle_id` (paranoia — should be none on first run).
  2. Pass 2: for records missing `handle_id`, generate one against the running set, **write the JSON back atomically** (this is the only command that mutates JSON during rebuild — call it out in the log line), then upsert into SQLite.
  3. Pass 3: rebuild `aliases` table from scratch as today.
- Add `--dry-run` to `rebuild-index` showing what would change without writing.
- Add a `--no-backfill` escape hatch that skips the JSON write-back (rebuilds DB only with whatever `handle_id` is in the JSON).

### Gate
1. Run `rebuild-index --dry-run` on a pre-v1.1 library: reports N records would gain `handle_id`, no files modified.
2. Run `rebuild-index`: all records have `handle_id` in JSON and DB; running it again is a no-op (idempotent).
3. SQLite `papers.handle_id` column is fully populated and `UNIQUE` constraint holds.

---

## 7. Phase 5 — `resolve_id`, `list`, `show` use `handle_id` (P1)

### Problem
Currently `resolve_id` accepts `paper_id` (`p_...`), aliases (`doi:...`), and bare 16-char hashes. It does not recognize `handle_id`.

### Fix
- **`resolve_id` dispatch order** (must be exact to avoid ambiguity):
  1. Starts with `p_` → treat as `paper_id`.
  2. Contains `:` → treat as alias namespace (`doi:`, `arxiv:`, `hash:`).
  3. Matches 16-character lowercase hex → treat as a bare hash → look up via `aliases` namespace `hash`.
  4. Otherwise → look up `handle_id` in `papers`.
  5. Not found → raise `IdNotFound` with a hint listing the namespaces tried.
- **`list` command:** Add a `handle_id` column (default ON; hide via `--no-handle`). Sort by `handle_id` when `--sort=handle` is passed.
- **`show` command:** Display `handle_id` prominently at the top alongside `paper_id`.

### Gate
1. `paperlib show smith_2014` returns the same record as `paperlib show p_<hash>`.
2. Ambiguity tests: a `handle_id` that happens to be 16 hex chars is still resolved as a handle (because step 3 looks up the `hash` alias first, which won't match). Document this corner in a code comment.
3. `paperlib list` shows the new column; `--sort=handle` works.

---

## 8. Phase 6 — Enforce `review.locked` in ingest + `mark-reviewed` (P1)

### Problem
The schema has `review.locked: bool` but the ingest pipeline never reads it. Re-ingesting a paper with locked fields silently overwrites them.

### Fix
- **Ingest enforcement:**
  - Before writing the record, if a record with this `paper_id` already exists *and* `review.locked == True`, do **not** overwrite locked fields. Specifically, walk each `MetadataField` in the existing record; if it has `locked=True`, copy it verbatim into the new record, ignoring whatever AI/embedded extraction produced.
  - Log a warning when this happens: `"<handle_id>: 4 locked fields preserved"`.
  - If the **entire** record is locked (a top-level convention: `review.locked == True` on the record), skip writing entirely and bump a counter in `IngestReport`.
- **`mark-reviewed` command:** New CLI command.
  - Args: `<id> [--field <name>]... [--unlock]`.
  - Without `--field`: locks the whole record (`review.locked=True`, `review.reviewer`, `review.reviewed_at`).
  - With one or more `--field`: locks only those specific `MetadataField`s.
  - `--unlock` reverses the operation.
  - Writes JSON atomically, then updates SQLite.

### Gate
1. Ingest test: lock `title` on a record, re-ingest the same PDF — `title` is preserved.
2. Ingest test: lock the full record, re-ingest — record is untouched, `IngestReport.locked_skipped` increments.
3. `mark-reviewed smith_2014 --field title --field authors` correctly sets `locked=True` on those two fields only.
4. `mark-reviewed smith_2014 --unlock` clears all locks.

---

## 9. Phase 7 — Interactive `review` command (P1)

### Problem
No user-facing way to correct AI mistakes inline.

### Fix
- New `paperlib review <id>` command.
- Loads the record, prints each editable `MetadataField` with its current value, source, and confidence.
- Prompts the user (via an injected `input_func` to keep it testable) for a new value, blank to keep, or `!` to lock without changing.
- After all fields, asks for confirmation, then writes JSON + DB atomically.
- `--field <name>` restricts to a single field.
- All edits set `source="user"`, `confidence=1.0`, `locked=True`, `updated_at=<now>`.

### Gate
1. Unit tests with a mock `input_func` cover: blank-keeps, value-overwrites, `!`-locks-only, full skip.
2. Integration test: `review` then `ingest` — reviewed fields survive.
3. Logged to `logs/ingest.log` with the user's username (or `"unknown"`).

---

## 10. Phase 8 — Multi-provider AI (Anthropic / OpenAI / OpenRouter) (P1)

### Problem
`ai/client.py::call_anthropic` is hard-wired to the Anthropic SDK. Users want to choose provider per-library — Anthropic for quality, OpenAI for cost, OpenRouter for breadth (Llama, DeepSeek, Gemini, etc.). The change must be additive and zero-risk for existing Anthropic users.

### Design (key decisions)

1. **Provider selected via a prefix in the `model` config string.** No new top-level `provider` field is required; the existing `AIConfig.provider` is left for backwards-compat but ignored when the model string carries a prefix.
   - `model = "claude-sonnet-4-5"` → Anthropic (back-compat: no prefix means Anthropic).
   - `model = "anthropic:claude-sonnet-4-5"` → Anthropic (explicit).
   - `model = "openai:gpt-4o"` → OpenAI.
   - `model = "openrouter:meta-llama/llama-3.3-70b-instruct"` → OpenRouter (OpenAI-compatible).
   - `model = "openai-compat:my-model"` → generic OpenAI-compatible (requires `base_url`).

2. **Two backends, three providers.** OpenAI and OpenRouter share one code path because OpenRouter is OpenAI-API-compatible. Net result: one Anthropic SDK call site, one OpenAI SDK call site.

3. **Two new optional config fields** in `[ai]`:
   - `base_url` — overrides the SDK default. Required for `openai-compat:`; optional for `openrouter:` (we ship the default `https://openrouter.ai/api/v1`).
   - `api_key_env` — name of the environment variable to read. Defaults: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` chosen by prefix.

4. **Optional dependency.** Add `openai` to `pyproject.toml` as an extra: `paperlib[openai]`. If the user configures an OpenAI/OpenRouter model without installing it, raise `AIError("install paperlib[openai] to use this provider")` — never crash with a bare `ImportError`.

5. **Single error type.** All provider failures map to the existing `AIError`. The summarise pipeline's catch-all stays unchanged; AI failure still degrades gracefully to `summary.status="failed"`.

### Fix
- **`ai/client.py`:**
  - Add a small parser: `(provider, model) = split_model_string(s)`.
  - Add `call_openai_compatible(prompt, *, model, base_url, api_key) -> str` that uses the `openai` SDK in chat-completions mode. Wrap the import in a try/except that re-raises as `AIError` with the install hint.
  - Rename existing `call_anthropic` → keep the name; signature unchanged.
  - Add a top-level `call_ai(prompt, ai_config) -> str` dispatcher. This becomes the only function the pipeline imports.
- **`config.py`:**
  - `AIConfig` gains `base_url: str | None = None`, `api_key_env: str | None = None`. Both optional.
  - Validation: if `model` starts with `openai-compat:` and `base_url` is missing → fail config validation early with a clear message.
  - Resolve `api_key_env` to a default at config-load time based on the prefix, so downstream code never has to guess.
- **`pipeline/summarise.py`:**
  - Replace the `call_anthropic(...)` invocation with `call_ai(prompt, ai_config)`. One line.
- **`pyproject.toml`:**
  - Add `[project.optional-dependencies]` group `openai = ["openai>=1.0"]`.
  - Anthropic remains a hard dependency for back-compat.
- **Prompts:** `ai/prompts.py` is provider-agnostic — no change. Keep `SUMMARY_PROMPT_VERSION = "v1"`.
- **Logging:** Log the resolved provider + model + base_url (without the API key) at INFO level when AI is enabled.

### Config example (additive — full file lives in `config.example.toml`)
```toml
[ai]
enabled     = true
model       = "openrouter:meta-llama/llama-3.3-70b-instruct"
base_url    = "https://openrouter.ai/api/v1"   # optional; default for openrouter
api_key_env = "OPENROUTER_API_KEY"              # optional; default by prefix
max_tokens  = 1200
temperature = 0.2
```

### Gate
1. **Anthropic regression:** Existing Anthropic config (`model = "claude-sonnet-4-5"` with no prefix) produces identical output before and after. Same prompt, same JSON shape, same `apply_ai_output_to_record` result. Snapshot test.
2. **OpenAI smoke test:** Live call against `gpt-4o` (or smallest available chat model) returns valid JSON parsed by `parse_model_output` without changes.
3. **OpenRouter smoke test:** Live call against a free OpenRouter model (e.g. `meta-llama/llama-3.3-70b-instruct:free`) returns valid JSON.
4. **Missing dependency:** With `openai` SDK uninstalled and `model = "openai:gpt-4o"`, the run produces a single record with `summary.status="failed"` and reason mentioning the install command — the batch does not abort.
5. **Missing API key:** With `OPENAI_API_KEY` unset, same graceful failure with a clear log line.
6. **Bad base_url:** With `model = "openai-compat:foo"` and no `base_url`, config validation fails at startup (not during ingest).
7. **`ai/prompts.py` unchanged.** Diff check.

### Non-goals (deferred to v1.2)
- Streaming responses.
- Structured-output / JSON-mode flags. We continue parsing fenced-or-bare JSON from the text response, which works on all three providers today.
- Per-paper provider override (CLI flag). All papers in one ingest run use one provider.
- Tool-use / function-calling. Not needed for the summarise prompt.
- Cost/token accounting. The fields exist in the schema but are not populated reliably across providers.

---

## 11. Phase 9 — CLI help + `__about__.py` (P2)

- Add `src/paperlib/__about__.py` defining `__version__`, `__title__`.
- Wire `paperlib --version` to read it.
- Audit every Click command's `help=` text; ensure each subcommand has a one-line summary and a clear description of its flags.
- Add `--config` as a global option (currently per-subcommand) — but keep the per-subcommand version working as a deprecation alias for one release.

### Gate
1. `paperlib --version` prints the version.
2. `paperlib --help` lists all commands with one-line summaries.
3. `paperlib <cmd> --help` is informative for every command.

---

## 12. Phase 10 — CHANGELOG + README + config.example.toml (P2)

- Write `CHANGELOG.md` following Keep a Changelog. v1.1 entry covers all phases above.
- Update `README.md`:
  - Mention `handle_id` in the "Identifiers" section.
  - Update the AI section to describe the three providers, the prefix convention, and the optional `[openai]` extra.
  - Update "Known limitations" — the `--no-ai` author-extraction hole is fixed; remove that bullet.
  - Add a "Reviewing records" section pointing at `mark-reviewed` and `review`.
- Update `config.example.toml`:
  - Add commented-out `base_url` and `api_key_env` lines under `[ai]`.
  - Add a comment block listing the four model-prefix forms.
- Update `implement_plan.md` if it's the canonical spec — append a v1.1 changelog section, do not rewrite v1 history.

### Gate
1. README's documented behavior matches the actual CLI (manual smoke test of every example).
2. `config.example.toml` validates via `paperlib validate-config`.
3. CHANGELOG is dated and links to migration notes for the SQLite v1 → v2 bump.

---

## 13. Cross-phase test additions

These tests are added incrementally during their owning phase but run as a single suite:

- **Phase 1:** four PDF-metadata-extraction unit tests, two filename-heuristic tests, one end-to-end `--no-ai` integration test.
- **Phase 2:** assert `logs/ingest.log` non-empty after each command in the existing CLI test fixture.
- **Phase 3–5:** `handle_id` generation collision test, `resolve_id` dispatch test (5 namespaces), `list`/`show` snapshot tests.
- **Phase 6–7:** lock-preservation integration test, `mark-reviewed` round-trip, `review` mock-input test.
- **Phase 8:** snapshot test for Anthropic regression, two live smoke tests gated by env-var presence (`PAPERLIB_TEST_OPENAI=1`, `PAPERLIB_TEST_OPENROUTER=1`) so CI without API keys still passes.

Target: existing 161 tests stay green; new total ≥ 200.

---

## 14. Migration notes (single-shot, in the implementation guide)

For users upgrading from v1.0 to v1.1:

1. `pip install --upgrade paperlib` (and `paperlib[openai]` if switching providers).
2. `paperlib rebuild-index` once. This:
   - Bumps SQLite schema to v2.
   - Backfills `handle_id` into both JSON and DB.
   - Rebuilds aliases.
3. Optional: edit `config.toml` to set a new `model` with a provider prefix.
4. No file moves, no re-extraction, no re-AI calls.

If `rebuild-index` fails halfway, it's safe to re-run — atomic JSON writes + SQLite transactions ensure no partial state survives.

---

## 15. Out of scope for v1.1 (parked)

- First-page text-based title/author extraction (regex/heuristic on `raw_text`). Deferred — embedded metadata + filename covers ~80 % of common papers; text parsing is fragile on two-column physics/CS PDFs and not worth the complexity gate for v1.1.
- BibTeX export.
- Web UI / TUI.
- Cross-library merge.
- Provider-aware token/cost accounting.
- Streaming AI responses.
- Per-paper AI provider override.

These will be triaged into v1.2 after v1.1 ships.

---

## 16. Phase gates summary (one-line each)

1. `--no-ai --limit 3` produces records with non-null author/title/year on ≥ 2/3 PDFs; canonical paths follow `{author}_{year}_{hash8}.pdf`.
2. `logs/ingest.log` is non-empty after any command run.
3. New ingests have `handle_id`; old records still load.
4. `rebuild-index` backfills `handle_id` idempotently.
5. `paperlib show <handle>` resolves to the right record across all 5 namespaces.
6. Locked fields survive re-ingest; `mark-reviewed` round-trips.
7. `paperlib review <id>` edits + locks fields with mocked input.
8. Anthropic, OpenAI, OpenRouter all return valid JSON; missing optional dep fails gracefully.
9. `paperlib --version` and per-command `--help` are useful.
10. CHANGELOG, README, `config.example.toml` match shipped behavior.

When all 10 gates pass on a clean checkout against the sanity corpus, v1.1 is shippable.
