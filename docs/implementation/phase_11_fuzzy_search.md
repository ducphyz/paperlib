# Phase 11 — Fuzzy Search

Full spec: [`v1_3_plan.md § Phase 11`](../../v1_3_plan.md)

## Goal

Catch typos and near-matches in metadata fields using RapidFuzz. Complements FTS5 for
queries like `"resnator"` (resonator), `"feromagnetic"` (ferromagnetic), `"bogolubov"`
(Bogoliubov).

## Prerequisites

- Phase 8 — normalization applied to query before fuzzy matching.
- Records loaded in memory (fuzzy runs over in-memory metadata, not SQL).

## Files to create

| File | Action |
|---|---|
| `src/paperlib/search/fuzzy.py` | Create — `fuzzy_search()` |

---

## Implementation

### Result types — `search/models.py`

Add:

```python
@dataclass
class FuzzyHit:
    paper_id: str
    field: str           # "title" | "authors" | "tags" | "materials" | "devices" | "aliases"
    matched_value: str   # the actual string that matched
    score: float         # RapidFuzz score 0–100
```

### `fuzzy_search` — `search/fuzzy.py`

```python
from rapidfuzz import fuzz, process

def fuzzy_search(
    query: str,
    records: list[PaperRecord],
    aliases: dict[str, list[str]],
    *,
    threshold: int = 85,
) -> list[FuzzyHit]:
```

Steps:
1. Normalize query via `normalize_query(query)`.
2. For each record, extract candidate strings from these fields:
   - `metadata["title"].value`
   - `metadata["authors"].value` (as list of strings or single string)
   - `summary["tags"]` (list)
   - `summary["physics"]["materials"]` (list)
   - `summary["physics"]["devices"]` (list)
   - `identity.aliases` — `hash:`, `doi:`, `arxiv:` aliases excluded; only
     user-facing name aliases.
3. For each candidate string, compute `fuzz.WRatio(query, candidate)`.
4. If score ≥ `threshold`: append `FuzzyHit(paper_id, field, candidate, score)`.
5. Return all hits with score ≥ threshold, sorted by score descending.

**Does not run over chunk text.** Chunk text is prohibitively large for fuzzy matching.

All fuzzy hits are labeled so they do not dominate exact FTS matches in the ranking
phase. The ranking formula (Phase 13) assigns fuzzy a weight of 0.02.

### `--field` restriction (from Phase 14)

When `--field title` is set: only match against title.
When `--field authors` is set: only match against authors.
When `--field all` or `--field summary`: match all fields listed above.

---

## Edge cases

- `metadata["title"].value` is `None`: skip.
- Authors stored as a list vs. a joined string: normalize to individual strings before
  matching.
- `threshold=85` is the default. The CLI does not expose a `--threshold` flag in v1.3.
- Same paper can appear multiple times (different fields matched): that's fine. The
  ranking phase deduplicates by `paper_id` and takes the best score.

---

## Tests required

`tests/test_fuzzy.py` (new):
- Typo `"resnator"` matches a paper with `"resonator"` in title.
- `"feromagnetic"` matches `"ferromagnetic"`.
- `"bogolubov"` matches `"Bogoliubov"`.
- Score below threshold → no hit.
- `--field title` restricts to title field only.

---

## Acceptance criteria

- [ ] `fuzzy_search(query, records, aliases, threshold=85) -> list[FuzzyHit]` in
  `search/fuzzy.py`.
- [ ] Runs over title, authors, tags, materials, devices, identity aliases only.
- [ ] Does not run over chunk text.
- [ ] `FuzzyHit` dataclass in `search/models.py`.
- [ ] Results sorted by score descending.
- [ ] `--field` restriction respected.
