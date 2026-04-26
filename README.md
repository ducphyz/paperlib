# paperlib

`paperlib` is a local Python CLI tool for building a structured personal paper
library from PDFs.

It scans an `inbox/`, validates PDFs, extracts and cleans text, detects DOI and
arXiv identifiers, assigns stable internal `paper_id` values, writes canonical
JSON records, and maintains a rebuildable SQLite index. Optional Anthropic AI
can fill selected metadata fields and generate structured summaries.

## v1 Scope

In scope for v1:

- scan `inbox/`
- validate PDFs
- extract and clean text
- detect DOI and arXiv ID
- assign stable `paper_id`
- move PDFs to canonical locations
- write extracted text files
- write JSON records
- update SQLite
- optionally generate AI summaries
- provide CLI commands for ingesting, inspecting, and rebuilding the library

Out of scope for v1:

- OCR
- RAG
- embeddings
- GUI
- Crossref, arXiv, or Semantic Scholar lookups
- fuzzy duplicate detection

## Installation

Python 3.14.3 is the supported project runtime. The recommended setup uses a
conda environment:

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

Edit `config.toml` and set `library.root` to an existing directory that will
hold the paper library.

Set `ANTHROPIC_API_KEY` in `.env` only if you plan to use AI summaries.
Non-AI commands do not require an API key.

## Quick Start

Create the library root directory, then let `validate-config` create the runtime
subdirectories:

```bash
paperlib validate-config
```

Put PDFs into the library `inbox/`, then inspect what would happen:

```bash
paperlib ingest --dry-run
```

Run a non-AI ingest:

```bash
paperlib ingest --no-ai
```

Inspect the library:

```bash
paperlib status
paperlib list
paperlib show <paper_id>
```

## AI Usage

Use `--no-ai` to avoid AI entirely:

```bash
paperlib ingest --no-ai
```

Without `--no-ai`, `paperlib ingest` attempts AI summarization when
`ai.enabled = true` in `config.toml`.

If the API key is missing, non-AI commands still work. AI summary generation may
fail or be skipped depending on the command path, but records remain valid and
the ingest continues where possible.

## CLI Reference

```bash
paperlib validate-config
paperlib ingest
paperlib ingest --dry-run
paperlib ingest --no-ai
paperlib ingest --limit N
paperlib status
paperlib list
paperlib list --needs-review
paperlib show <id_or_alias>
paperlib rebuild-index
```

`show` accepts a `paper_id` such as `p_abc123...` or a stored alias such as
`arxiv:2401.12345`, `doi:10.xxxx/example`, or `hash:<hash16>`.

## Source of Truth

JSON records in `records/` are canonical. SQLite is an index and can be rebuilt
from JSON records with:

```bash
paperlib rebuild-index
```

## Safety

Unknown metadata is stored as `null`. `paperlib` must not fabricate missing
metadata; placeholders such as `unknown_year` and `unknown_author` are used only
for filenames, not metadata values.

## Documentation

- [Architecture](docs/architecture.md)
- [Schema](docs/schema.md)
- [Configuration](docs/config.md)
- [Operations](docs/operations.md)
- [Limitations](docs/limitations.md)
- [Roadmap](docs/roadmap.md)
