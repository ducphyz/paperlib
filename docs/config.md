# Configuration

`paperlib` loads settings from `config.toml` and reads environment variables
from `.env` next to that config file.

Create local files:

```bash
cp config.example.toml config.toml
cp .env.example .env
```

## config.toml Structure

```toml
[library]
root = "/Users/you/paperlib"

[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
deleted = "deleted"
duplicates = "duplicates"

[pipeline]
move_after_ingest = true
skip_existing = true
dry_run_default = false

[extraction]
engine = "pdfplumber"
min_char_count = 500
min_word_count = 100

[ai]
enabled = true
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2

[lookup]
enabled = false
mailto = ""
timeout_sec = 5.0
```

## Keys

`library.root`

The existing root directory for the paper library. `validate-config` requires
this directory to already exist.

`paths.inbox`

Directory scanned for incoming PDFs.

`paths.papers`

Directory where ingested PDFs are moved using canonical filenames.

`paths.records`

Directory where canonical JSON records are written.

`paths.text`

Directory where cleaned extracted text files are written.

`paths.db`

SQLite database path.

`paths.logs`

Runtime log directory.

`paths.failed`

Directory where invalid or unreadable PDFs are moved.

`paths.deleted`

Directory where PDFs moved by `paperlib delete` are stored.

`paths.duplicates`

Runtime directory reserved for duplicate handling.

`pipeline.move_after_ingest`

Configured pipeline flag for moving PDFs after ingest. v1 ingest moves valid
PDFs to their canonical location.

`pipeline.skip_existing`

Configured pipeline flag for skipping existing files. v1 deduplicates exact
files by checking whether the file hash is already indexed in SQLite.

`pipeline.dry_run_default`

Configured default dry-run flag. The CLI currently uses the explicit
`--dry-run` option for dry runs.

`extraction.engine`

Extraction engine name. The implemented engine is `pdfplumber`.

`extraction.min_char_count`

Minimum character count used when classifying extraction quality.

`extraction.min_word_count`

Minimum word count used when classifying extraction quality.

`ai.enabled`

Enables AI summarization for plain `paperlib ingest` when set to `true`.

`ai.model`

AI model name used for summaries. Prefixes route requests to Anthropic,
OpenAI, OpenRouter, or a generic OpenAI-compatible endpoint.

`ai.max_tokens`

Maximum tokens requested from the AI provider.

`ai.temperature`

Temperature passed to the AI provider.

`ai.provider`

Legacy field retained for backwards compatibility. If set to `"anthropic"`,
routes requests to Anthropic regardless of the model prefix. Prefer the
model-prefix routing (`anthropic:`, `openai:`, `openrouter:`, `openai-compat:`)
in new configs.

`ai.base_url`

Custom base URL for OpenAI-compatible endpoints. Required when using
`openai-compat:<model>`. Optional for `openrouter:` (defaults to
`https://openrouter.ai/api/v1`).

`ai.api_key_env`

Name of the environment variable to read the API key from. Overrides the
provider default (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`).
Useful for local or custom endpoints.

`lookup.enabled`

Set `true` to call Crossref and arXiv during ingest. The default is `false`
for offline/local-only use.

`lookup.mailto`

Optional email address added to the Crossref `User-Agent` header for polite
pool access. Leave empty to omit it.

`lookup.timeout_sec`

HTTP timeout in seconds for lookup calls.

## .env

`.env` may define:

```bash
ANTHROPIC_API_KEY=...
```

Set `ANTHROPIC_API_KEY` only if using AI summaries. Do not commit real API keys.

## validate-config Behavior

`paperlib validate-config`:

- loads `config.toml` and `.env`
- requires `library.root` to already exist
- exits with an error if `library.root` is missing
- creates missing runtime subdirectories under `library.root`
- prints a path status table
- warns, but does not fail, if `ai.enabled = true` and `ANTHROPIC_API_KEY` is
  missing

`validate-config` does not create `library.root`. This prevents a typo in the
root path from silently creating a new library.

## Path Resolution

Relative path values in `[paths]` are resolved under `library.root`. Absolute
path values remain absolute.

The loaded `AppConfig` stores resolved `pathlib.Path` objects for
`library.root` and all configured paths.

## AI Key Behavior

`paperlib ingest --no-ai` does not require `ANTHROPIC_API_KEY`.

If `ai.enabled = false`, ingest does not require `ANTHROPIC_API_KEY`.

Plain `paperlib ingest` attempts AI summarization when `ai.enabled = true`. If
the configured provider key is missing, affected summaries fail, records are
still written, and non-AI commands continue to work.
