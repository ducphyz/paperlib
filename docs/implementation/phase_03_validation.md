# Phase 3 — Markdown Validation

Full spec: [`v1_3_plan.md § Phase 3`](../../v1_3_plan.md)

## Goal

Reject malformed AI output before it enters the search index. Classify extraction
results as `"validated"`, `"partial"`, or `"failed"` so the chunker and orchestrator
can decide what to index.

## Prerequisites

- Phase 1 — `MarkdownExtractionResult` dataclass must exist.

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/pipeline/markdown_extractors/validate.py` | Create |

---

## Implementation

### `validate_markdown` — `pipeline/markdown_extractors/validate.py`

```python
def validate_markdown(text: str | None, page_count: int | None) -> tuple[str, list[str]]:
    """
    Returns (status, errors) where status is one of:
        "validated"  — all checks pass
        "partial"    — usable but imperfect (soft signals triggered)
        "failed"     — not indexable
    """
```

### Required checks (in order)

**Hard fails — any one → `"failed"`:**

1. **Non-empty:** `text` is `None` or `len(text.strip()) == 0` → fail.
2. **Near-empty:** fewer than 10 words total → fail.
3. **At least one page marker present** (`<!-- page: N -->` format) → if none found → fail.
4. **No API refusal detected:** scan only the **first ~1000 characters** for phrases like
   `"I cannot"`, `"As an AI"`, `"I'm sorry"`. Scanning the full document risks false
   positives from quoted paper text. Any match → fail.

**Structural checks — failures here → `"partial"`:**

5. **Page marker format:** markers must exactly match `<!-- page: N -->` where N is a
   positive integer. No alternatives accepted: `Page 1`, `[Page 1]`, `<!--page:1-->`,
   `<!-- Page: 1 -->`. Any non-conforming marker found alongside conforming ones →
   partial. (If no markers at all, this is already caught by check 3.)
6. **First page marker is `<!-- page: 1 -->`:** if the first marker found is not N=1 →
   partial.
7. **Monotonically increasing page numbers:** extract all N from `<!-- page: N -->`;
   if any N[i] ≤ N[i-1] → partial. Duplicates also count as non-monotonic.
8. **References section for long papers:** if `page_count >= 10` (or `page_count is None`
   and the document has ≥ 10 pages worth of markers), check for a heading matching
   `## References` or `## Bibliography` (case-insensitive). Missing → partial.
9. **At least one `##` heading present:** no `##` headings at all → partial.
10. **Suspiciously short output per page:** if `page_count` is known and `word_count /
    page_count < 50`, this is a soft signal toward partial. Extremely figure-heavy
    papers, short letters, and equation-dense PDFs legitimately trigger this. Do not
    hard-fail on this alone; set partial if no other hard check failed.

### Status resolution

```
all hard checks pass AND no soft signals → "validated"
all hard checks pass AND ≥ 1 soft signal → "partial"
any hard check fails → "failed"
```

A single failed hard check produces `"failed"` regardless of soft signals. Multiple
errors accumulate in the `errors` list.

### Error messages

Each check that fails or degrades the result adds a human-readable string to `errors`,
e.g.:
- `"output is empty"`
- `"API refusal detected in first 1000 characters"`
- `"no page markers found"`
- `"page markers not monotonically increasing: [3, 2, 4]"`
- `"first page marker is 5, expected 1"`
- `"no ## headings found"`
- `"no References section found (>= 10 pages)"`
- `"suspiciously short: 32 words/page (< 50 threshold)"`

---

## Edge cases

- `page_count = None` (e.g. encrypted PDF): skip the words-per-page heuristic. Still
  check for references section if the document itself has ≥ 10 page markers.
- Quoted paper text that contains phrases like "I cannot" (e.g. "I cannot stress enough
  how important...") — this is why refusal detection is limited to the first 1000 chars.
- Papers with a single page: `page_count = 1`, no references section expected.
- Non-monotonic markers: `[1, 2, 2, 3]` — duplicate 2 counts as non-monotonic.
- Empty string vs. whitespace-only: both count as empty (check 1).

---

## Tests required

`tests/test_validate_markdown.py` (new):
- Empty / None / near-empty input → `"failed"`.
- Refusal phrase in first 1000 chars → `"failed"`.
- Refusal phrase after 1000 chars (quoted paper text) → does not fail.
- Missing page markers → `"failed"`.
- Wrong marker format (`Page 1`, `<!--page:1-->`) → `"partial"`.
- Non-monotonic markers → `"partial"`.
- First marker not 1 → `"partial"`.
- Missing references for 10+ page paper → `"partial"`.
- No `##` headings → `"partial"`.
- All checks pass → `"validated"`.
- Multiple soft signals → still `"partial"` (not `"failed"`).
- `page_count=None` with < 10 page markers — no references check.

---

## Acceptance criteria

- [ ] `validate_markdown(text, page_count) -> tuple[str, list[str]]` exists in
  `pipeline/markdown_extractors/validate.py`.
- [ ] Refusal detection limited to first ~1000 characters only.
- [ ] Exact page marker format `<!-- page: N -->` is the only accepted form.
- [ ] Non-monotonic or non-1-starting markers → `"partial"` not `"failed"`.
- [ ] Empty / near-empty / refusal / no-markers → `"failed"`.
- [ ] `errors` list contains one entry per failing check.
