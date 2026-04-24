# paperlib

`paperlib` is a local Python CLI tool for managing a personal PDF paper library.

Version 1 will scan PDFs, extract text, detect basic identifiers, write JSON records, maintain a rebuildable SQLite index, and optionally generate AI metadata and summaries.

## Current status

Phase 1 skeleton only.

Implemented:

- package structure
- configuration loading
- runtime directory validation
- logging setup
- basic filesystem helpers
- `paperlib validate-config`

Not implemented yet:

- PDF discovery
- PDF validation
- text extraction
- metadata extraction
- JSON persistence
- SQLite index
- AI summary generation

## Development setup

```bash
conda create -n paperlib python=3.14.3 -y
conda activate paperlib
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

## Configuration

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` so that `library.root` points to an existing directory.

## First command

```bash
paperlib validate-config
```