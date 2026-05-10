# Phase 13 — Hybrid Ranking

Full spec: [`v1_3_plan.md § Phase 13`](../../v1_3_plan.md)

## Goal

Combine FTS5, fuzzy, and semantic signals into a final ranked list with score breakdowns
and human-readable explanations.

## Prerequisites

- Phase 10 — `FtsHit` results.
- Phase 11 — `FuzzyHit` results.
- Phase 12 — `SemanticHit` results.
- Phase 8 — normalization (used for multi-concept bonus term extraction).

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/search/ranking.py` | Create — `rank()` |
| `src/paperlib/search/models.py` | Add `SearchResult`, `ScoreBreakdown` dataclasses |

---

## Implementation

### `SearchResult` and `ScoreBreakdown` — `search/models.py`

```python
@dataclass
class ScoreBreakdown:
    paper_fts: float = 0.0
    chunk_fts: float = 0.0
    paper_semantic: float = 0.0
    chunk_semantic: float = 0.0
    structured_field: float = 0.0
    alias: float = 0.0
    fuzzy: float = 0.0

@dataclass
class SearchResult:
    paper_id: str
    handle_id: str | None
    title: str | None
    year: int | None
    score: float
    score_breakdown: ScoreBreakdown
    why: list[str]
    relevant_chunks: list[ChunkHit]   # top chunks for display
```

### `rank` — `search/ranking.py`

```python
def rank(
    fts_hits: list[FtsHit],
    fuzzy_hits: list[FuzzyHit],
    semantic_hits: list[SemanticHit],
    query: str,
    records: dict[str, PaperRecord],   # paper_id → record, for metadata lookup
) -> list[SearchResult]:
```

#### Score formula

All component scores normalized to 0–1 before combining.

```
final = (
    0.20 * paper_fts_score
  + 0.20 * chunk_fts_score
  + 0.25 * paper_semantic
  + 0.20 * chunk_semantic
  + 0.10 * structured_field_match   # exact hit in materials/devices/phenomena/quantities
  + 0.03 * alias_match              # alias expansion triggered a hit
  + 0.02 * fuzzy_match
)
```

Cap `final` at 1.0.

#### Normalization

- FTS5 rank is negative (more negative = better). Convert: `score = 1 / (1 + abs(rank))`,
  then normalize across all hits so max = 1.0.
- Fuzzy score is 0–100 from RapidFuzz. Normalize: `score / 100`.
- Semantic score is a dot product in [0, 1] (vectors are normalized). Already in range.

#### Structured field match (0.10 component)

Check if the normalized query (or any expanded alias term) appears as an exact substring
in any of: `materials`, `devices`, `phenomena`, `quantities`, `tags`. Score: 1.0 if any
match, 0.0 otherwise. This is a binary signal, not a ranked one.

#### Alias match (0.03 component)

1.0 if any alias expansion was used that contributed to a hit (i.e. an expanded term
appeared in FTS or semantic results). 0.0 otherwise.

#### Multi-concept bonus

1. Strip common English stop words from the normalized query to produce meaningful terms:
   remove words like `a`, `an`, `the`, `in`, `of`, `and`, `or`, `is`, `for`, `with`,
   `on`, `at`, `by`. No noun-phrase extraction — keep it simple.
2. For each paper in the result set, count how many distinct meaningful terms appear
   anywhere in the paper's indexed fields (title, authors, tags, materials, devices,
   phenomena, quantities, methods, key_contributions, summary text).
3. If count ≥ 2: add +0.10 additive bonus to `final` score.
4. Cap total at 1.0.

#### Why-matched explanations

Build one plain-English string per match reason. Examples:
- `"matched devices: CPW resonator"` — from structured field match
- `"matched chunk in section 'Device Design' (page 3-4)"` — from chunk FTS or semantic
- `"fuzzy match: resnator → resonator"` — from fuzzy hit
- `"alias expansion: cpw → coplanar waveguide resonator"` — when alias triggered a hit
- `"chunk location approximate (txt source)"` — when chunk has `location_confidence == "low"`

Collect all applicable reasons for a paper into `SearchResult.why`.

#### Deduplication

Multiple hits from different signals for the same `paper_id` are merged into one
`SearchResult`. Scores from different signals are combined via the formula above.

---

## Edge cases

- A paper appears in FTS hits but not semantic hits (no embeddings): its semantic
  component score is 0.0. This is correct — no penalty, just no contribution.
- All signals return zero hits: return `[]`.
- Scores very close together: preserve deterministic ordering (e.g. secondary sort by
  `paper_id`).
- `records` dict does not contain a `paper_id` from hits (record deleted after search
  started): skip that hit.

---

## Tests required

`tests/test_ranking.py` (new):
- Weight formula produces correct combined score for known inputs.
- Multi-concept bonus of +0.10 applied when ≥ 2 distinct terms match.
- Score capped at 1.0.
- Paper with only FTS hits (no semantic) gets correct score (semantic components = 0).
- `why` list contains appropriate entries.

---

## Acceptance criteria

- [ ] `rank(fts_hits, fuzzy_hits, semantic_hits, query, records) -> list[SearchResult]`
  in `search/ranking.py`.
- [ ] Score formula matches weights exactly as specified.
- [ ] Multi-concept bonus: +0.10 for ≥ 2 distinct meaningful terms, capped at 1.0.
- [ ] `ScoreBreakdown` and `SearchResult` dataclasses in `search/models.py`.
- [ ] `why` list populated with human-readable strings.
- [ ] Results sorted by `score` descending; deterministic tie-breaking.
