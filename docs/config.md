# Configuration

`paperlib` loads configuration from `config.toml` and environment variables
from `.env`.

Create local files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

## Required Library Root

`library.root` must point to an existing directory:

```toml
[library]
root = "/Users/you/PaperLibrary"
```

`paperlib validate-config` does not create `library.root`; this prevents typos
from silently creating a new library. It does create missing runtime
subdirectories under the root.

## Paths

Path values are resolved relative to `library.root` unless absolute:

```toml
[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
duplicates = "duplicates"
```

## Pipeline

```toml
[pipeline]
move_after_ingest = true
skip_existing = true
dry_run_default = false
```

Existing files are skipped by hash when already indexed in SQLite.

## Extraction

```toml
[extraction]
engine = "pdfplumber"
min_char_count = 500
min_word_count = 100
```

`pdfplumber` is the implemented extraction engine.

## AI

```toml
[ai]
enabled = true
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2
```

Set `ANTHROPIC_API_KEY` in `.env` only when using AI:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

`paperlib ingest --no-ai` does not require an API key. Non-AI commands still
work when the key is missing.
