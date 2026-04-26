# Operations

This guide covers day-to-day `paperlib` v1 usage.

## First Setup

Create a library root directory:

```bash
mkdir -p ~/PaperLibrary
```

Create local config files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` and set `library.root` to the library root:

```toml
[library]
root = "/Users/you/PaperLibrary"
```

Validate the config and create runtime subdirectories:

```bash
paperlib validate-config
```

`library.root` must already exist. `validate-config` creates missing runtime
paths such as `inbox/`, `records/`, `text/`, `papers/`, `db/`, `logs/`,
`failed/`, and `duplicates/`.

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

Run the ingest without AI:

```bash
paperlib ingest --no-ai
```

Check the resulting library:

```bash
paperlib status
paperlib list
paperlib show <paper_id>
```

`show` also accepts aliases such as:

```bash
paperlib show arxiv:2401.12345
paperlib show doi:10.xxxx/example
paperlib show hash:<hash16>
```

## AI Ingest

To use AI summaries, set an Anthropic API key in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
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

Summary status values:

- `generated`: AI summary was generated and stored.
- `failed`: AI was attempted but failed; ingest still wrote the record.
- `skipped`: AI was not used, usually because `--no-ai` was passed, AI was
  disabled, or the summary was locked.

## Rebuild SQLite

Use `rebuild-index` when `db/library.db` is missing, stale, or should be
recreated after manual JSON edits:

```bash
paperlib rebuild-index
```

If the database already exists, `paperlib` first writes a timestamped backup
next to it, then rebuilds the index from `records/*.json`.

JSON remains the source of truth. SQLite can be deleted and recreated from the
JSON records.

## Manual Record Editing

Manual edits should be made in:

```text
records/{paper_id}.json
```

Edit carefully:

- Preserve `schema_version`.
- Keep valid JSON.
- Use `locked: true` on metadata or summary fields that should not be changed
  by later AI runs.
- Do not replace unknown metadata with placeholder strings; keep unknown values
  as `null`.

After manual JSON changes, rebuild SQLite:

```bash
paperlib rebuild-index
```

## Handling Failed PDFs

Invalid, unreadable, or broken PDFs are moved to:

```text
failed/
```

Inspect these files manually. If a file can be fixed, move it back into
`inbox/` and run the ingest flow again:

```bash
mv ~/PaperLibrary/failed/example.pdf ~/PaperLibrary/inbox/
paperlib ingest --dry-run
paperlib ingest --no-ai
```

## Duplicate Behavior

Exact duplicate files are detected by full SHA-256 hash. If a file hash already
exists in SQLite, the file is skipped.

DOI and arXiv aliases are used for paper-level duplicate detection. If a new
file has a DOI or arXiv ID matching an existing record, the file is attached to
that same record instead of creating a new paper.

## Safe Workflow

For each new batch:

```bash
paperlib ingest --dry-run
paperlib ingest --limit 3 --no-ai
paperlib status
paperlib list
```

After the small test batch looks right, run the full ingest:

```bash
paperlib ingest --no-ai
```

Use `--limit N` whenever testing a new library, config, or batch of unfamiliar
PDFs.
