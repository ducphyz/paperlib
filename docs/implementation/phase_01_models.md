# Phase 1 — Data Models

Full spec: [`v1_3_plan.md § Phase 1`](../../v1_3_plan.md)

## Goal

Establish the data structures before writing any extraction or search code. All
downstream phases depend on these types being present.

## Prerequisites

- Phase 15 (config) should be done concurrently or immediately after this phase,
  as `ExtractionConfig` markdown fields are consumed in Phase 2.

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/models/file.py` | Add `MarkdownInfo` dataclass; add `markdown` field to `FileRecord` |
| `src/paperlib/models/record.py` | Add `phenomena`, `quantities`, `aliases` to `_default_summary()["physics"]` |
| `src/paperlib/pipeline/markdown_extractors/__init__.py` | Create (empty) |
| `src/paperlib/pipeline/markdown_extractors/base.py` | Create — `MarkdownExtractionResult`, `MarkdownExtractor` Protocol |

---

## Implementation

### `MarkdownExtractionResult` — `pipeline/markdown_extractors/base.py`

```python
from dataclasses import dataclass, field

@dataclass
class MarkdownExtractionResult:
    success: bool
    markdown: str | None
    provider: str            # "openai_pdf" | "claude_pdf" | ...
    model: str
    source: str              # "api_pdf"
    validation_status: str   # "unvalidated" on return from provider; set by orchestrator
    validation_errors: list[str]
    page_count: int | None
    created_at: str
```

Providers return this with `validation_status = "unvalidated"` and
`validation_errors = []`. The orchestrator (Phase 2) calls `validate_markdown()` and
fills those fields before persisting anything.

### `MarkdownExtractor` Protocol — `pipeline/markdown_extractors/base.py`

```python
from typing import Protocol
from pathlib import Path

class MarkdownExtractor(Protocol):
    def extract(self, pdf_path: Path, config) -> MarkdownExtractionResult:
        ...
```

`config` here is `AppConfig` (imported lazily or typed with `TYPE_CHECKING` to avoid
circular imports).

### `MarkdownInfo` — `models/file.py`

```python
@dataclass
class MarkdownInfo:
    markdown_path: str | None   # None when status == "failed" (no file written)
    provider: str               # "openai_pdf" | "claude_pdf" | ...
    model: str
    status: str                 # "validated" | "partial" | "failed"
    validation_errors: list[str]
    page_count: int | None
    created_at: str

    def to_dict(self) -> dict:
        return {
            "markdown_path": self.markdown_path,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "validation_errors": list(self.validation_errors),
            "page_count": self.page_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MarkdownInfo":
        return cls(
            markdown_path=data.get("markdown_path"),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            status=data.get("status", "failed"),
            validation_errors=list(data.get("validation_errors", [])),
            page_count=data.get("page_count"),
            created_at=data.get("created_at", ""),
        )
```

### `FileRecord` — `models/file.py`

Add `markdown: MarkdownInfo | None = None` as a field with default `None`, so all
existing records deserialise without error.

Update `to_dict()`:
```python
def to_dict(self) -> dict:
    d = {
        "file_hash": self.file_hash,
        # ... existing fields ...
        "extraction": self.extraction.to_dict(),
    }
    if self.markdown is not None:
        d["markdown"] = self.markdown.to_dict()
    return d
```

Update `from_dict()`:
```python
@classmethod
def from_dict(cls, data: dict) -> "FileRecord":
    markdown_data = data.get("markdown")
    return cls(
        # ... existing fields ...
        markdown=MarkdownInfo.from_dict(markdown_data) if markdown_data else None,
    )
```

### Physics summary fields — `models/record.py`

In `_default_summary()`, extend the `"physics"` dict:

```python
"physics": {
    "field": None,
    "materials": [],
    "devices": [],
    "measurements": [],
    "main_theory": [],
    "phenomena": [],    # add
    "quantities": [],   # add
    "aliases": [],      # add
},
```

**Why this must be in Phase 1:** `_merge_dict` in `from_dict` only passes through keys
that exist in the defaults dict. Adding these fields after any other phase risks silently
dropping them on every read/write cycle for existing records.

---

## Edge cases

- `FileRecord.markdown` must default to `None` so records written before v1.3 round-trip
  cleanly without the `markdown` key present.
- `MarkdownInfo.to_dict()` omits no keys — even `None` values are serialised — so
  `from_dict` can rely on `.get()` with sensible defaults.
- The three new physics list fields default to `[]`. Existing records that lack these
  keys get the default on next read; they are not written back until the record is
  otherwise modified.

---

## Tests required

Phase 1 structures are tested indirectly by later phase tests. A direct test in
`tests/test_record.py` (already exists) should verify:

- `_default_summary()["physics"]` contains `phenomena`, `quantities`, `aliases` keys.
- `FileRecord` with no `markdown` key round-trips via `to_dict()` / `from_dict()` with
  `markdown=None`.
- `FileRecord` with a `markdown` dict round-trips to `MarkdownInfo` and back.

---

## Acceptance criteria

- [ ] `MarkdownExtractionResult` and `MarkdownExtractor` exist in
  `pipeline/markdown_extractors/base.py`.
- [ ] `MarkdownInfo` exists in `models/file.py` with `to_dict` / `from_dict`.
- [ ] `FileRecord` has `markdown: MarkdownInfo | None = None`; serialises only when
  non-None; deserialises from missing key as `None`.
- [ ] `_default_summary()["physics"]` includes `phenomena`, `quantities`, `aliases`.
- [ ] Existing test suite (`pytest`) still passes.
