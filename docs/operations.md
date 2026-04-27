# Operations

This guide covers practical `paperlib` v1 usage.

## First Setup

Create the library root:

```bash
mkdir -p ~/PaperLibrary
```

Create local config files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` and set `library.root`:

```toml
[library]
root = "/Users/you/PaperLibrary"
```

Validate the config:

```bash
paperlib validate-config
```

`library.root` must already exist. `validate-config` creates missing runtime
subdirectories such as `inbox/`, `papers/`, `records/`, `text/`, `db/`,
`logs/`, `failed/`, and `duplicates/`.

## Normal Non-AI Ingest

Copy PDFs into the configured inbox:

```bash
cp ~/Downloads/*.pdf ~/PaperLibrary/inbox/
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
mv ~/PaperLibrary/failed/example.pdf ~/PaperLibrary/inbox/
paperlib ingest --dry-run
paperlib ingest --no-ai
```

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
