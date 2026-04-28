# AGENTS.md

## Project

`paperlib` — local Python CLI that ingests PDFs into a structured academic library.
PDFs are discovered from `inbox/`, validated, text-extracted, optionally AI-summarised
(Anthropic API), then stored with canonical filenames, JSON records, and a SQLite index.

**JSON records are canonical. SQLite is a rebuildable index.**  
Rebuild at any time with `paperlib rebuild-index`.

---

## Setup

```bash
pip install -e ".[dev]"   # requires Python 3.14.3
cp .env.example .env      # set ANTHROPIC_API_KEY
cp config.example.toml config.toml  # set library.root
```

## Tests

```bash
pytest
pytest --cov
pytest tests/test_foo.py  # single file
```

No linter or CI configured. Match style of the file you're editing.

---

## Layout

```
src/paperlib/
  cli.py            CLI entry point — all user-facing output lives here only
  config.py         Loads config.toml + .env → AppConfig dataclass
  handle.py         Generates human-readable handle_id (e.g. smith_2023)
  models/           PaperRecord, PaperIdentity, MetadataField, FileRecord
  pipeline/         discover → validate → extract → clean → metadata → summarise → ingest
  store/            db.py (SQLite), fs.py (filesystem), json_store.py (atomic JSON),
                    migrations.py (schema v2)
  ai/               client.py (Anthropic wrapper), prompts.py (summary prompt)
tests/              One test file per module; fixtures in tests/fixtures/
docs/               architecture.md, schema.md, config.md, operations.md
```

---

## Invariants — do not break

1. **Pipeline modules return data; never print.** All `click.echo` output is confined
   to `cli.py`. Raise or return from pipeline code; never print.
2. **JSON is truth.** `records/{paper_id}.json` wins over SQLite on any conflict.
3. **Atomic writes only.** Use temp file → `fsync` → rename (see `store/fs.py`).
   No bare `open(..., 'w')` for any library data file.
4. **SQLite writes use transactions.** See `store/db.py`. No bare writes outside a transaction.
5. **AI never overwrites locked fields.** `metadata.year` is never AI-sourced.
   Respect `locked: true` on any metadata field or summary.
6. **`paper_id` is permanent.** Assigned as `p_{sha256[:16]}` on first ingest; never reassigned.
7. **`--dry-run` is strictly read-only.** No file moves, no JSON/text writes, no SQLite
   updates, no AI calls. Stop at discovery + validation only.
8. **AI failures are non-fatal.** Write the record anyway; set `status.summary = "failed"`.

---

## Data model

`PaperRecord` (`models/record.py`) is the canonical shape:

| Field          | Notes                                             |
|----------------|---------------------------------------------------|
| `paper_id`     | `p_{hash16}`, immutable                           |
| `handle_id`    | Human-readable, e.g. `smith_2023`                 |
| `identity`     | DOI, arXiv ID, aliases (`hash:`, `doi:`, `arxiv:`)|
| `files`        | One `FileRecord` per ingested file                |
| `metadata`     | title, authors, year, journal — each a `MetadataField` with `value`, `source`, `locked`, `confidence` |
| `summary`      | AI summary blob; has its own `locked` flag        |
| `status`       | metadata / summary / duplicate / review statuses  |
| `review`       | notes, locked, reviewed_at                        |
| `timestamps`   | created_at, updated_at                            |

---

## CLI

```
paperlib ingest [--dry-run] [--no-ai]
paperlib rebuild-index
paperlib status | list | show <id> | mark-reviewed <id>
paperlib validate-config
```

---

## v1 scope — do not add

No OCR, no external metadata APIs (CrossRef, Semantic Scholar, etc.), no embeddings,
no RAG, no web UI. Scope changes belong in `docs/roadmap.md`.

---

## Before submitting

- `pytest` must pass.
- New pipeline stage → add `tests/test_<stage>.py`.
- Modified `PaperRecord` or JSON shape → update `docs/schema.md` and check `store/migrations.py`.
- Never commit `config.toml`, `.env`, or files under the library root.
