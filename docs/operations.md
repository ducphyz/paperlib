# Operations

This guide covers practical `paperlib` v1 usage.

## First Setup

Create the library root:

```bash
mkdir -p ~/paperlib
```

Create local config files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` and set `library.root`:

```toml
[library]
root = "/Users/you/paperlib"
```

Validate the config:

```bash
paperlib validate-config
```

`library.root` must already exist. `validate-config` creates missing runtime
subdirectories such as `inbox/`, `papers/`, `records/`, `text/`, `db/`,
`logs/`, `failed/`, `deleted/`, and `duplicates/`.

## Normal Non-AI Ingest

Copy PDFs into the configured inbox:

```bash
cp ~/Downloads/*.pdf ~/paperlib/inbox/
```

Inspect the batch without writing files, moving PDFs, updating SQLite, or
calling AI:

```bash
paperlib ingest --dry-run
```

Run ingest without AI:

```bash
paperlib ingest --no-ai
```

Check the library:

```bash
paperlib status
paperlib list
paperlib show <paper_id>
```

`show` also accepts stored aliases:

```bash
paperlib show arxiv:2401.12345
paperlib show doi:10.xxxx/example
paperlib show hash:<hash16>
```

## AI Ingest

Set an Anthropic API key in `.env`:

```bash
ANTHROPIC_API_KEY=...
```

Enable AI in `config.toml`:

```toml
[ai]
enabled = true
```

Run ingest without `--no-ai`:

```bash
paperlib ingest
```

Summary statuses:

- `generated`: AI summary was generated and stored.
- `failed`: AI was attempted but failed; ingest continued and wrote the record.
- `skipped`: AI was not used because `--no-ai` was passed, AI was disabled, or
  the summary was locked.

## Rebuild SQLite

Use `rebuild-index` when `db/library.db` is missing, stale, or should be
recreated after manual JSON edits:

```bash
paperlib rebuild-index
```

If the database already exists, `paperlib` writes a timestamped backup next to
it before rebuilding.

JSON records remain the source of truth. SQLite is rebuilt from
`records/*.json`.

## Manual Record Editing

Manual edits are made in:

```text
records/{paper_id}.json
```

Edit carefully:

- Preserve `schema_version`.
- Keep the file valid JSON.
- Use `locked: true` on metadata fields or summaries that should not be
  overwritten by later automated runs.
- Keep unknown metadata values as `null`.

After manual JSON changes, rebuild SQLite:

```bash
paperlib rebuild-index
```

## Handling Failed PDFs

Invalid, unreadable, or broken PDFs are moved to:

```text
failed/
```

Inspect failed files manually. If a file can be fixed, move it back to
`inbox/` and run the normal ingest flow again:

```bash
mv ~/paperlib/failed/example.pdf ~/paperlib/inbox/
paperlib ingest --dry-run
paperlib ingest --no-ai
```

## Deleting a Paper

Remove a paper from the active library:

```bash
paperlib delete <handle_id>
```

The PDF is moved to `deleted/`. The SQLite rows for the paper and its files are
removed, and the JSON record and extracted text file are deleted.

To undo a delete, move the PDF back to `inbox/` and run `paperlib ingest`.

## Validating the Library

Check that JSON records, SQLite index, PDFs, and text files are consistent:

```bash
paperlib validate-library
```

Findings are reported by severity (`error` or `warning`) and category:

| Category | Meaning |
|---|---|
| MISSING_DB | SQLite database file not found |
| BAD_JSON | JSON record file is invalid or unreadable |
| JSON_NOT_IN_DB | JSON record has no matching SQLite row |
| DB_NOT_IN_JSON | SQLite row points to a JSON file that does not exist |
| MISSING_PDF | Canonical PDF path in JSON does not exist on disk |
| MISSING_TEXT | Text file path in JSON does not exist on disk |
| ORPHAN_PDF | PDF in `papers/` not referenced by any JSON record |

For DB/JSON inconsistencies, run `paperlib rebuild-index` to repair. Missing
PDFs and text files require manual recovery.

## Re-summarising Records

Re-run AI summarisation for records whose summary status is `skipped` or
`failed`:

```bash
paperlib re-summarise
paperlib re-summarise <handle_id>   # single record
paperlib re-summarise --limit 5     # process at most 5 records
paperlib re-summarise --no-ai       # mark all as skipped without calling AI
```

AI must be enabled in `config.toml` and `ANTHROPIC_API_KEY` or the configured
provider key must be set. Summary failures remain non-fatal.

## Exporting BibTeX

Export all records as BibTeX:

```bash
paperlib export --bibtex
```

Export specific records:

```bash
paperlib export --bibtex <handle_id> [<handle_id> ...]
```

Write to a file:

```bash
paperlib export --bibtex --output refs.bib
```

Entry type is `@article` when a DOI is present, `@misc` otherwise. The cite key
is the record's `handle_id` when available, otherwise `paper_id`.

## Searching the Library

Search by title, authors, or summary text:

```bash
paperlib search "Feynman"
paperlib search "quantum" --field title
paperlib search "Einstein" --field authors
paperlib search "spin qubit" --field summary
paperlib search "Josephson" --field all
```

`--field all` searches title and authors in SQLite and also scans JSON
summaries. Output format matches `paperlib list`.

## Duplicate Behavior

Exact duplicate files are detected by full SHA-256 hash. If the hash already
exists in SQLite, the file is skipped.

DOI and arXiv aliases are used for paper-level duplicate detection. If a new
file has a DOI or arXiv ID matching an existing record, the file is attached to
that same record instead of creating a new paper.

## Safe Workflow

Always run a dry run first on a new batch:

```bash
paperlib ingest --dry-run
```

Start with a small limited ingest when testing a new library, config, or batch:

```bash
paperlib ingest --limit 3 --no-ai
paperlib status
paperlib list
```

After the small batch looks right, run the full non-AI ingest:

```bash
paperlib ingest --no-ai
```
