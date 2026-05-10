# Phase 15 — Config Additions

Full spec: [`v1_3_plan.md § Phase 15`](../../v1_3_plan.md)

## Goal

Extend `config.py` with `SearchConfig` and markdown-related `ExtractionConfig` fields.
Update `AppConfig` to include `search`. Update `config.example.toml` and docs.

**This phase should be implemented before or alongside Phase 1.** Almost every
subsequent phase reads from `config.search` or `config.extraction` markdown fields.

## Prerequisites

- None — this is pure configuration scaffolding.

## Files to modify

| File | Action |
|---|---|
| `src/paperlib/config.py` | Add `SearchConfig`; extend `ExtractionConfig`; add `AppConfig.search`; update `load_config` |
| `config.example.toml` | Add `[extraction]` markdown keys and `[search]` section |
| `docs/config.md` | Document all new keys |

---

## Implementation

### `ExtractionConfig` — `config.py`

Extend with markdown fields:

```python
@dataclass
class ExtractionConfig:
    engine: str
    min_char_count: int
    min_word_count: int
    # v1.3 additions
    markdown_backend: str           # "openai_pdf" | "none"; default "none"
    markdown_model: str             # e.g. "openai:gpt-5.4"; provider prefix required
    markdown_require_validation: bool   # default True
    markdown_fallback_to_txt: bool      # default True
    markdown_fallback_partial: bool     # default False
```

### `SearchConfig` — `config.py`

```python
@dataclass
class SearchConfig:
    embedding_backend: str   # "local"; default "local"
    embedding_model: str     # default "sentence-transformers/all-MiniLM-L6-v2"
    alias_file: str          # "" = use bundled; non-empty = user file path override
    default_mode: str        # "hybrid"; "keyword"|"fuzzy"|"semantic"|"hybrid"
    top_n: int               # default 10
```

### `AppConfig` — `config.py`

```python
@dataclass
class AppConfig:
    library: LibraryConfig
    paths: PathsConfig
    pipeline: PipelineConfig
    extraction: ExtractionConfig
    ai: AIConfig
    lookup: LookupConfig
    search: SearchConfig      # new
```

### `load_config` — `config.py`

Extend to read `[extraction]` markdown fields and the new `[search]` section:

```python
extraction=ExtractionConfig(
    engine=str(extraction_data.get("engine", "pdfplumber")),
    min_char_count=int(extraction_data.get("min_char_count", 500)),
    min_word_count=int(extraction_data.get("min_word_count", 100)),
    markdown_backend=str(extraction_data.get("markdown_backend", "none")),
    markdown_model=str(extraction_data.get("markdown_model", "")),
    markdown_require_validation=bool(extraction_data.get("markdown_require_validation", True)),
    markdown_fallback_to_txt=bool(extraction_data.get("markdown_fallback_to_txt", True)),
    markdown_fallback_partial=bool(extraction_data.get("markdown_fallback_partial", False)),
),
```

```python
search_data = _section(data, "search")
search=SearchConfig(
    embedding_backend=str(search_data.get("embedding_backend", "local")),
    embedding_model=str(search_data.get("embedding_model",
        "sentence-transformers/all-MiniLM-L6-v2")),
    alias_file=str(search_data.get("alias_file", "")),
    default_mode=str(search_data.get("default_mode", "hybrid")),
    top_n=int(search_data.get("top_n", 10)),
),
```

### `markdown_model` validation (strict)

At config load time, validate `markdown_model` with stricter rules than `ai.model`:

1. A provider prefix is **required**. Unprefixed strings do NOT route to Anthropic and
   instead raise `ConfigError`:
   ```
   ConfigError: extraction.markdown_model requires a provider prefix (e.g. 'openai:gpt-5.4')
   ```
2. Only `openai` or `openai-compat` are accepted. Any other value raises `ConfigError`:
   ```
   ConfigError: extraction.markdown_model provider must be 'openai' or 'openai-compat';
   got 'anthropic'. Use openai-compat with a base_url for OpenRouter.
   ```

This applies to: `anthropic`, `openrouter`, unprefixed strings.

If `markdown_backend = "none"` and `markdown_model` is empty, skip validation (no model
needed when backend is disabled).

```python
def _validate_markdown_model(model: str, backend: str) -> None:
    if backend == "none" or not model:
        return
    try:
        provider, _ = split_model_string(model)
    except AIError as exc:
        raise ConfigError(f"extraction.markdown_model: {exc}") from exc
    if provider not in ("openai", "openai-compat"):
        raise ConfigError(
            f"extraction.markdown_model provider must be 'openai' or 'openai-compat'; "
            f"got '{provider}'. Use openai-compat with base_url for OpenRouter."
        )
```

### `config.example.toml`

Add these sections (or extend existing `[extraction]`):

```toml
[extraction]
engine            = "pdfplumber"
# Markdown extraction settings (v1.3)
# Set markdown_backend to "openai_pdf" to enable. Default "none" avoids API cost.
markdown_backend  = "none"
markdown_model    = "openai:gpt-5.4"   # prefix required; only openai or openai-compat
markdown_require_validation  = true
markdown_fallback_to_txt     = true
markdown_fallback_partial    = false

[search]
embedding_backend  = "local"
embedding_model    = "sentence-transformers/all-MiniLM-L6-v2"
alias_file         = ""      # blank → uses bundled search_aliases.toml
default_mode       = "hybrid"
top_n              = 10
```

---

## Edge cases

- `[search]` section absent from `config.toml`: `_section(data, "search")` returns `{}`
  and all defaults apply. No `ConfigError`.
- `markdown_model` set but `markdown_backend = "none"`: skip validation (model is
  unused).
- `alias_file` non-empty but path does not exist: raise `ConfigError` at load time (or
  defer to first alias load — at load time is preferred).
- `default_mode` with invalid value: validate against the allowed set at load time.

---

## Tests required

`tests/test_config.py` (extend existing):
- `SearchConfig` loaded with correct defaults when `[search]` is absent.
- Extended `ExtractionConfig` fields parsed correctly.
- `markdown_model` with missing prefix raises `ConfigError`.
- `markdown_model = "anthropic:claude-4"` raises `ConfigError`.
- `markdown_model = "openrouter:..."` raises `ConfigError`.
- `markdown_model = "openai-compat:..."` is accepted.
- `search.alias_file` override accepted and stored.
- `AppConfig.search` present with correct type.
- `markdown_backend = "none"` with empty `markdown_model`: no error.

---

## Acceptance criteria

- [ ] `SearchConfig` dataclass with all five fields and correct defaults.
- [ ] `ExtractionConfig` extended with five markdown fields.
- [ ] `AppConfig.search: SearchConfig` field present.
- [ ] `load_config` reads `[search]` section and constructs `SearchConfig`.
- [ ] `markdown_model` validated at load time: prefix required; only `openai` /
  `openai-compat`; `anthropic`, `openrouter`, unprefixed → `ConfigError`.
- [ ] Validation skipped when `markdown_backend = "none"` and model is empty.
- [ ] `config.example.toml` updated with all new keys including the `openai:` prefix on
  `markdown_model`.
- [ ] `docs/config.md` updated with all v1.3 config keys, defaults, and valid values.
