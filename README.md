# paperlib

paperlib is a local-first Python CLI for ingesting, indexing, reviewing, and
summarizing academic papers. It watches a configured `inbox/`, validates PDFs,
extracts text and embedded metadata, creates canonical JSON records, moves PDFs
to stable filenames, and maintains a rebuildable SQLite index.

The current implementation is focused on a personal physics paper library, but
the storage model is intentionally plain: JSON files are the source of truth and
SQLite is only an index.

## Architecture

paperlib stores each paper as a JSON record under `records/`. Those JSON records
are canonical. If JSON and SQLite disagree, JSON wins.

SQLite is rebuildable:

```bash
paperlib rebuild-index
```

The two main identifiers are:

- `paper_id`: immutable internal identity, assigned as `p_<hash16>` on first
  ingest.
- `handle_id`: human-friendly identity such as `smith_2014`, generated from
  author and year when possible.

paperlib also stores aliases such as `doi:...`, `arxiv:...`, and
`hash:<hash16>` for lookup.

## Installation

Use a Python environment matching the supported runtime:

```bash
conda create -n paperlib python=3.14.3 -y
conda activate paperlib
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

## Configuration

Create local configuration files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` and set `library.root` to the existing directory that should
hold the paper library. `validate-config` creates runtime subdirectories such as
`inbox/`, `papers/`, `records/`, `text/`, `db/`, `logs/`, `failed/`, and
`deleted/`.

```bash
paperlib validate-config --config config.toml
```

The checked-in `config.example.toml` is intentionally self-validating from the
repository root. Change `library.root` before using it for a real library.

## Basic Workflow

Validate the configuration:

```bash
paperlib validate-config
```

Put PDFs into the configured `inbox/`, inspect the batch, then ingest a small
non-AI sample:

```bash
paperlib ingest --dry-run
paperlib ingest --no-ai --limit 3
```

Inspect records:

```bash
paperlib list
paperlib show <handle_id>
```

Rebuild SQLite from JSON at any time:

```bash
paperlib rebuild-index
```

Review records:

```bash
paperlib mark-reviewed <handle_id>
paperlib review <handle_id>
```

Search, export, validate, retry summaries, or delete records:

```bash
paperlib search "Feynman"
paperlib export --bibtex
paperlib validate-library
paperlib re-summarise
paperlib delete <handle_id>
```

## Identifiers

`paper_id` is permanent and internal. It is derived from the first 16 hex
characters of the PDF hash, for example:

```text
p_0440c911081cc43b
```

`handle_id` is for humans. It is generated from the first author surname and
year when available:

```text
smith_2014
smith_2014_b
paper_0440c911
```

Use `paperlib show` with any supported identifier:

```bash
paperlib show smith_2014
paperlib show p_0440c911081cc43b
paperlib show doi:10.1234/example
paperlib show arxiv:2401.12345
paperlib show hash:0440c911081cc43b
```

`paperlib list` shows `handle_id` by default. Use `--no-handle` to hide it or
`--sort handle` to sort by handle.

## Ingest Behavior

Non-AI ingest uses embedded PDF metadata and conservative filename heuristics to
populate title, authors, and year when possible. Unknown metadata remains
`null`; paperlib does not fabricate values.

Canonical PDF paths keep the year directory and use author-first filenames:

```text
papers/2014/smith_2014_abcd1234.pdf
```

When AI is enabled, AI output may fill unlocked metadata fields and generate a
structured summary. AI never overwrites locked metadata fields.

## Review Workflow

New records start with `status.review = "needs_review"`.

Use `paperlib review <id>` for an interactive metadata review. Blank input keeps
the current value, a new value is stored as `source = "user"` with confidence
`1.0`, and `!` locks an existing metadata value without changing it.

Use `paperlib mark-reviewed <id>` to mark the whole record as reviewed. This
sets:

```text
status.review = "reviewed"
review.locked = true
```

Locked metadata fields survive re-ingest. A fully locked record is skipped on
re-ingest so reviewed human edits are not overwritten.

## AI Configuration

AI provider selection is controlled by the `model` prefix in `[ai]`:

- No prefix: Anthropic, for backwards compatibility.
- `anthropic:...`: Anthropic.
- `openai:...`: OpenAI.
- `openrouter:...`: OpenRouter through the OpenAI-compatible API.
- `openai-compat:...`: any OpenAI-compatible endpoint; requires `base_url`.

Default API key environment variables:

- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- OpenRouter: `OPENROUTER_API_KEY`
- OpenAI-compatible: `OPENAI_API_KEY` unless `api_key_env` is set.

Examples:

```toml
[ai]
enabled = true
model = "claude-sonnet-4-20250514"

# model = "anthropic:claude-sonnet-4-5"
# model = "openai:gpt-4o"
# model = "openrouter:meta-llama/llama-3.3-70b-instruct"
# model = "openai-compat:local-model"
# base_url = "http://localhost:11434/v1"
# api_key_env = "LOCAL_AI_KEY"
```

AI failures are non-fatal. Ingest continues, writes the record, and marks the
summary as failed.

## CLI Reference

```bash
paperlib --version
paperlib --help
paperlib --config config.toml ingest --no-ai --limit 3
paperlib validate-config
paperlib ingest
paperlib ingest --dry-run
paperlib ingest --no-ai
paperlib ingest --limit N
paperlib status
paperlib list
paperlib list --needs-review
paperlib list --sort handle
paperlib show <id_or_alias>
paperlib delete <id_or_alias>
paperlib rebuild-index
paperlib rebuild-index --dry-run
paperlib rebuild-index --no-backfill
paperlib mark-reviewed <id_or_alias>
paperlib review <id_or_alias>
paperlib validate-library
paperlib re-summarise
paperlib export --bibtex
paperlib search QUERY
paperlib search QUERY --field title|authors|summary|all
paperlib search QUERY --sort year|handle
```

Per-command `--config` remains supported:

```bash
paperlib ingest --config config.toml --no-ai
```

## Known Limitations

- No OCR. Scanned PDFs are detected but not text-extracted.
- No first-page text title/author heuristic; v1.2 uses embedded metadata
  and conservative filename heuristics.
- No vector database, embeddings, or RAG.
- Crossref and arXiv lookup is available with `[lookup] enabled = true`.
  Semantic Scholar is not implemented.
- Embedded metadata is often incomplete or wrong and may still require review.
- No web UI or TUI.
- Provider-aware token and cost accounting is not implemented.

## Documentation

- [Architecture](docs/architecture.md)
- [Schema](docs/schema.md)
- [Configuration](docs/config.md)
- [Operations](docs/operations.md)
- [Limitations](docs/limitations.md)
- [Roadmap](docs/roadmap.md)
- [Source Inventory](docs/source_inventory.md)
- [Changelog](CHANGELOG.md) for release history, including package version 1.2.0
