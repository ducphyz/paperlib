# Phase 14 — Search CLI and JSON Output

Full spec: [`v1_3_plan.md § Phase 14`](../../v1_3_plan.md)

## Goal

Extend the existing `paperlib search` command with mode selection, JSON output, empty
index guard, and backward-compatible `--field`/`--sort` flags. Add `service.py` as the
high-level search facade.

## Prerequisites

- Phases 10, 11, 12, 13 — all search components must be implemented.
- Phase 5 — `search_index_state` table must exist (for the empty-index guard).
- Phase 15 — `SearchConfig` must be present (`default_mode`, `top_n`).

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/search/service.py` | Create — `search()` high-level facade |
| `src/paperlib/cli.py` | Extend existing `search` command with new flags |

---

## Implementation

### `service.py` — `search/service.py`

```python
def search(
    query: str,
    conn,
    config,
    records: list[PaperRecord],
    *,
    mode: str = "hybrid",     # "keyword" | "fuzzy" | "semantic" | "hybrid"
    n: int = 10,
    field: str = "all",       # "title" | "authors" | "summary" | "all"
) -> tuple[list[SearchResult], list[str]]:
    """
    Returns (results, expanded_terms).
    expanded_terms: alias expansions for --json transparency output.
    """
```

Orchestrates:
1. Load aliases from config.
2. Normalize + expand query.
3. Depending on `mode`:
   - `"keyword"`: run `keyword_search`; fuzzy = []; semantic = [].
   - `"fuzzy"`: run `fuzzy_search`; fts = []; semantic = [].
   - `"semantic"`: run `semantic_search`; if no embeddings → raise (exit non-zero).
   - `"hybrid"`: run all three. If semantic fails (no embeddings), warn and continue
     with fts + fuzzy only.
4. Call `rank(fts_hits, fuzzy_hits, semantic_hits, query, records_dict)`.
5. Return top `n` results.

### CLI — extend `search_command` in `cli.py`

Replace the existing LIKE-based implementation with a call to `service.search()`.

**New flags:**

```
--mode    keyword | fuzzy | semantic | hybrid   (default: hybrid, from config.search.default_mode)
--top     INTEGER                               (default: 10, from config.search.top_n)
--json                                          (output JSON instead of table)
```

**Retained flags (backward compatible — no deprecation):**

```
--field   title | authors | summary | all       (filter; default: all)
--sort    year | handle                          (re-sort; default: by score)
```

#### Empty index guard

Before running any search, check `search_index_state`:

```sql
SELECT id FROM search_index_state WHERE id = 1
```

If no row found → exit non-zero:
```
Error: search index not built. Run `paperlib rebuild-search-index` first.
```

Do NOT fall back to the legacy LIKE search. Do NOT use `SELECT COUNT(*) FROM paper_fts`
(zero rows is valid for an empty library after a successful rebuild).

An empty `paper_fts` with a valid `search_index_state` row (zero-paper library) is NOT
an error.

#### `--field` mapping

| `--field` | `paper_fts` columns queried | Fuzzy fields | Chunk FTS |
|---|---|---|---|
| `title` | `title` only | title only | disabled |
| `authors` | `authors` only | authors only | disabled |
| `summary` | `summary_short`, `summary_technical` | none | enabled |
| `all` | all columns (default) | all fields | enabled |

Semantic search **always ignores `--field`** — it operates over full embeddings. Passing
`--field title --mode semantic` is accepted without error, but `--field` has no effect
on the semantic component. Document this in `--help`.

#### `--sort`

When `--sort` is explicitly passed: re-sort the top-n results by that field after
scoring. Default ordering is by relevance/score (not year). `--sort` is only applied
when the flag is explicitly provided.

### Terminal output (default)

```
Results for: "weird cpw waveguide resonator"
Expanded: cpw → coplanar waveguide resonator

  #  handle_id          year  score  why
  1  smith_2022         2022  0.91   matched devices: CPW resonator; chunk in §Device Design (p3-4)
  2  jones_2021         2021  0.78   alias: cpw → coplanar waveguide; matched materials: Al-InAs
  3  chen_2020          2020  0.65   semantic match; fuzzy: resnator → resonator [txt source]
```

### JSON output (`--json`)

```json
{
  "query": "weird cpw waveguide resonator",
  "normalized_query": "weird cpw waveguide resonator",
  "expanded_terms": ["cpw", "coplanar waveguide", "coplanar waveguide resonator"],
  "mode": "hybrid",
  "results": [
    {
      "paper_id": "p_xxx",
      "handle_id": "smith_2022",
      "title": "...",
      "year": 2022,
      "score": 0.91,
      "score_breakdown": {
        "paper_fts": 0.7,
        "chunk_fts": 0.8,
        "paper_semantic": 0.9,
        "chunk_semantic": 0.85,
        "structured_field": 0.6,
        "alias": 0.5,
        "fuzzy": 0.0
      },
      "why": [
        "matched devices: CPW resonator",
        "matched chunk in section 'Device Design' (page 3-4)"
      ],
      "relevant_chunks": [
        {
          "chunk_id": "p_xxx_c004",
          "section_title": "Device Design",
          "source_type": "markdown_api",
          "location_confidence": "high",
          "page_start": 3,
          "page_end": 4,
          "score": 0.82,
          "snippet": "..."
        }
      ]
    }
  ]
}
```

JSON schema is stable — no rich terminal escapes or ANSI color codes in JSON output.

---

## Edge cases

- `--mode semantic` with no embeddings → non-zero exit with actionable message.
- `--mode hybrid` with no embeddings → warning printed, keyword+fuzzy results returned,
  exit zero.
- `search_index_state` row missing → non-zero exit with "search index not built" message.
- Zero results after search: print "No results." (terminal) or `{"results": []}` (JSON).
- `--sort year` applied after relevance ranking: re-sorts the top-n by year, breaking
  ties by original score.

---

## Tests required

`tests/test_search_json.py` (new):
- JSON schema validates against known fixture query.
- `expanded_terms` present in JSON output when alias fired.
- `score_breakdown` contains all seven keys.
- `relevant_chunks` contains `source_type`, `location_confidence`, `page_start`,
  `page_end`, `snippet`.

`tests/test_search_degraded.py` (new):
- Hybrid mode with no embeddings: emits warning, returns keyword+fuzzy results.
- Semantic mode with no embeddings: exits non-zero.
- `paperlib search` with no `search_index_state` row: exits non-zero with "search index
  not built" message.
- Empty `paper_fts` with a valid state row (zero-paper library): does NOT trigger the
  error.

---

## Acceptance criteria

- [ ] `search(query, conn, config, records, mode, n, field) -> (results, expanded_terms)`
  in `search/service.py`.
- [ ] `--mode`, `--top`, `--json` flags added to `paperlib search`.
- [ ] `--field` and `--sort` retained; no deprecation.
- [ ] Empty index detected via `search_index_state`, not `paper_fts` row count.
- [ ] Missing `search_index_state` → non-zero exit; no LIKE fallback.
- [ ] Semantic mode exits non-zero if no valid embeddings; hybrid degrades gracefully.
- [ ] `--field` has no effect on semantic component; documented in `--help`.
- [ ] JSON output stable (no ANSI codes); includes `score_breakdown`, `expanded_terms`,
  `relevant_chunks` with `location_confidence`, `page_start`, `page_end`.
- [ ] Default sort is by score; `--sort` only applied when explicitly passed.
