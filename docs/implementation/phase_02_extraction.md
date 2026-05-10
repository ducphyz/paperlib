# Phase 2 — PDF → Markdown Extraction

Full spec: [`v1_3_plan.md § Phase 2`](../../v1_3_plan.md)

## Goal

Produce `text/<paper_id>.md` from the original PDF via the AI API. This is the
high-confidence text source that Phase 6 (chunking) prefers over `.txt` fallback.

## Prerequisites

- Phase 1 — `MarkdownExtractionResult`, `MarkdownInfo`, `MarkdownExtractor` Protocol,
  `FileRecord.markdown` field must all exist.
- Phase 3 — `validate_markdown()` is called by the orchestrator in this phase.
- Phase 15 — `ExtractionConfig` markdown fields (`markdown_backend`, `markdown_model`,
  `markdown_require_validation`, `markdown_fallback_to_txt`, `markdown_fallback_partial`)
  must be present in `AppConfig`.

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/ai/client.py` | Add `call_ai_with_pdf()` function |
| `src/paperlib/pipeline/markdown_extractors/openai_pdf.py` | Create — OpenAI PDF provider |
| `src/paperlib/pipeline/extract_md.py` | Create — orchestrator |
| `src/paperlib/cli.py` | Add `extract-markdown` command |

---

## Implementation

### Phase 2A — `call_ai_with_pdf` in `ai/client.py`

Add a new module-level function following the same pattern as `call_ai`:

```python
def call_ai_with_pdf(
    pdf_path: Path, prompt: str, model: str, ai_config
) -> str:  # raw Markdown string
```

Key rules:
- `model` is an **explicit parameter**, not read from `ai_config`. The caller
  (`openai_pdf.py`) passes `config.extraction.markdown_model`.
- Internally calls `split_model_string(model)` to parse the provider prefix and route.
- **Provider validation lives here**, not in the extractor:
  - `openai` and `openai-compat` → proceed.
  - Any other provider (including `anthropic`, `openrouter`) → raise `AIError` with a
    clear message:
    ```
    "anthropic provider does not support PDF input; use openai or openai-compat"
    ```
- All wire-format details (base64 encoding, file object creation, multipart upload) live
  inside `call_ai_with_pdf`. The extractor never handles these.
- For OpenAI: read the PDF as bytes, base64-encode, pass as an image-type content block
  or use the Files API depending on the OpenAI client version. Check the `openai` SDK
  docs for the current way to pass a PDF file as a message attachment.
- `timeout_s` should come from `ai_config.timeout` if present, or default to a generous
  value (e.g. 120 s) since PDF extraction is slower than text prompts.

### Phase 2B — `openai_pdf.py`

`pipeline/markdown_extractors/openai_pdf.py` — first and only v1.3 provider.

```python
from paperlib.pipeline.markdown_extractors.base import (
    MarkdownExtractor, MarkdownExtractionResult,
)

class OpenAIPdfExtractor:
    def extract(self, pdf_path: Path, config) -> MarkdownExtractionResult:
        from paperlib.ai.client import call_ai_with_pdf
        from paperlib.utils import utc_now

        raw = call_ai_with_pdf(
            pdf_path,
            prompt=_EXTRACTION_PROMPT,
            model=config.extraction.markdown_model,
            ai_config=config.ai,
        )
        return MarkdownExtractionResult(
            success=True,
            markdown=raw,
            provider="openai_pdf",
            model=config.extraction.markdown_model,
            source="api_pdf",
            validation_status="unvalidated",
            validation_errors=[],
            page_count=None,      # orchestrator overwrites this
            created_at=utc_now(),
        )
```

**Responsibility boundary:** this class extracts only. It does not validate. It returns
`validation_status = "unvalidated"` always. The orchestrator calls `validate_markdown()`
and fills those fields.

The extractor never reads `ai_config.model` or `config.ai.model`. It passes
`config.extraction.markdown_model` explicitly as `model`.

**Extraction prompt** (`_EXTRACTION_PROMPT`) should request:
- Page markers in the format `<!-- page: N -->` (exact; no alternatives accepted)
- Section headings as `## Heading`
- Abstract, figure captions, table captions
- Equations (best-effort)
- References section
- Clean Markdown with no explanatory preamble from the AI

### Orchestrator — `pipeline/extract_md.py`

```python
def extract_markdown(
    paper_id: str, config, *, force: bool = False, dry_run: bool = False
) -> MarkdownExtractionResult | None:
```

Steps:
1. Resolve the paper's JSON record.
2. Select the `FileRecord` to extract from (use `summary.source_file_hash` if present
   and valid, otherwise the first file).
3. **Skip** if `file_record.markdown` is already set, `status != "failed"`, and
   `force=False`. Return `None`.
4. If `dry_run=True`: return `None` without any AI call or write.
5. Pick provider from `config.extraction.markdown_backend`:
   - `"openai_pdf"` → `OpenAIPdfExtractor()`
   - `"none"` → raise `ConfigError("markdown_backend is 'none'; set it to 'openai_pdf'")`
6. Call `provider.extract(pdf_path, config)` to get `MarkdownExtractionResult`.
7. **Compute `page_count` locally** from the PDF (e.g. with `pdfplumber`) and overwrite
   `result.page_count`. API-reported page counts must not be trusted.
8. Call `validate_markdown(result.markdown, result.page_count)` → `(status, errors)`.
   Set `result.validation_status = status` and `result.validation_errors = errors`.
9. If `status != "failed"` (i.e. `"validated"` or `"partial"` when
   `config.extraction.markdown_fallback_partial`):
   - Write the Markdown atomically to `config.paths.text / f"{paper_id}.md"`.
   - Set `markdown_path` to that path as a string.
10. Build `MarkdownInfo` from the result and write it into `file_record.markdown`.
    If `status == "failed"`, set `markdown_path=None`.
11. Write the updated JSON record atomically via `store/json_store.py`.
12. Return the `MarkdownExtractionResult`.

### CLI — `cli.py`

```
paperlib extract-markdown <id>          # single paper by paper_id or handle_id
paperlib extract-markdown --all         # all papers missing validated markdown
paperlib extract-markdown --failed      # retry papers where markdown.status == "failed"
paperlib extract-markdown --force       # re-extract even if already validated
paperlib extract-markdown --dry-run     # resolve candidates, skip AI calls and all writes
```

- `--all` selects records where `file_record.markdown is None` or
  `file_record.markdown.status != "validated"`.
- `--failed` selects records where `file_record.markdown is not None` and
  `file_record.markdown.status == "failed"`. (Requires that failed extractions persist
  `MarkdownInfo` into the JSON record — not just logs.)
- `--force` re-extracts even if `status == "validated"`.
- A failed extraction still writes `MarkdownInfo(status="failed", markdown_path=None,
  validation_errors=[...])` into `FileRecord` and persists to JSON.

---

## Edge cases

- Provider raises `AIError` → catch, write `MarkdownInfo(status="failed")`, log,
  continue to next paper in batch mode. Never crash the batch.
- `pdf_path` does not exist → raise `FileNotFoundError` before any API call.
- `markdown_backend = "none"` → fail clearly with a `ConfigError` before any provider
  is instantiated.
- `page_count` from PDF reader returns `None` (e.g. encrypted PDF) → pass `None` to
  `validate_markdown`; heuristics that depend on `page_count` should degrade gracefully.
- Re-running `extract-markdown` on a paper that already has validated Markdown is a
  no-op unless `--force` is passed.

---

## Tests required

`tests/test_ai_client.py` (partial — extend existing file):
- `call_ai_with_pdf` calls `split_model_string(model)` internally.
- `model = "openai:gpt-5.4"` routes to OpenAI backend.
- Anthropic provider string raises `AIError` immediately.
- `openrouter:` prefix raises `AIError`.

`tests/test_extract_md.py` (new):
- Orchestrator skips extraction if `markdown.status == "validated"` and no `--force`.
- `--dry-run` returns `None` and makes no writes.
- Failed extraction writes `MarkdownInfo(status="failed")` to the record.
- `--failed` flag selects only records with `markdown.status == "failed"`.
- `page_count` from the PDF overwrites the provider-returned value.

---

## Acceptance criteria

- [ ] `call_ai_with_pdf(pdf_path, prompt, model, ai_config)` exists in `ai/client.py`.
- [ ] Anthropic / unsupported providers raise `AIError` inside `call_ai_with_pdf`.
- [ ] `openai_pdf.py` passes `config.extraction.markdown_model` as `model`; never reads
  `ai_config.model`.
- [ ] Orchestrator computes `page_count` locally; overwrites provider value.
- [ ] `paperlib extract-markdown` produces `.md` files at `text/<paper_id>.md`.
- [ ] Failed extraction writes `MarkdownInfo(status="failed", markdown_path=None)` and
  persists to JSON.
- [ ] `--failed` selects only records with `file_record.markdown.status == "failed"`.
- [ ] `--dry-run` makes no writes and no AI calls.
- [ ] Re-running without `--force` is idempotent when Markdown already validated.
