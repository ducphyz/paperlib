# Phase 10 — Keyword Search (FTS5)

Full spec: [`v1_3_plan.md § Phase 10`](../../v1_3_plan.md)

## Goal

Full-text search over paper metadata and chunk text using SQLite FTS5. Returns ranked
hits with snippets.

## Prerequisites

- Phase 5 — `paper_fts` and `chunk_fts` tables must exist.
- Phase 7 — `rebuild-search-index` must have run to populate them.
- Phase 8 — normalization and alias expansion applied to queries before FTS.

## Files to create

| File | Action |
|---|---|
| `src/paperlib/search/fts.py` | Create — `keyword_search()`, FTS query builder |

---

## Implementation

### Result types — `search/models.py`

Add to the existing models file:

```python
@dataclass
class FtsHit:
    paper_id: str
    paper_rank: float        # FTS5 rank from paper_fts
    chunk_rank: float        # aggregate FTS5 rank from chunk_fts (0.0 if no chunk hits)
    matched_fields: list[str]
    snippets: list[str]      # up to 3 snippets from chunk_fts
```

### `keyword_search` — `search/fts.py`

```python
def keyword_search(
    query: str, conn, aliases: dict[str, list[str]], *, n: int = 20
) -> list[FtsHit]:
```

Steps:

1. Normalize query via `normalize_query(query)`.
2. Expand aliases via `expand_query(normalized, aliases)`.
3. Build a safe FTS5 query string (see below).
4. Query `paper_fts` for paper-level hits.
5. Query `chunk_fts` for chunk-level hits; group by paper, keep top 3 snippets.
6. Merge and return up to `n` results.

### Safe FTS5 query builder

Physics queries contain syntax-sensitive characters (`-`, `/`, `±`, `:`, `(`) that
break raw FTS5 parsing — e.g. `p-wave`, `Al/InAs`, `p±ip`.

Rules:
- **Do not pass raw query strings to FTS5 MATCH.** Build the query programmatically.
- Tokenize the normalized+expanded query into terms.
- For each term, check if it contains FTS5-sensitive characters. If yes, wrap in double
  quotes: `"Al/InAs"`.
- For multi-word expansions from alias expansion (e.g. `"coplanar waveguide"`), use
  boolean `AND` between the tokens rather than forcing phrase quoting — this preserves
  recall when words appear in different order or with intervening words.
- Reserve phrase quoting for single terms where word order truly matters (proper names,
  chemical formulas).
- Combine terms with `OR` (different alias expansions of the same abbreviation are
  alternatives).

Example query builder output for `"cpw resonator"` after alias expansion:
```
"cpw" OR "coplanar waveguide" OR "coplanar waveguide resonator" OR "CPW resonator" AND "resonator"
```

### `paper_fts` query

```sql
SELECT paper_id, rank
FROM paper_fts
WHERE paper_fts MATCH ?
ORDER BY rank
LIMIT ?
```

`rank` from FTS5 is negative (more negative = better match). Normalize to 0–1 in the
ranking phase (Phase 13).

To restrict to specific columns (`--field` flag from Phase 14):

```sql
SELECT paper_id, rank
FROM paper_fts('{title}: cpw resonator')   -- column filter syntax
WHERE paper_fts MATCH ?
...
```

Or use the `{column}` FTS5 column filter prefix in the MATCH expression.

### `chunk_fts` query

```sql
SELECT chunk_id, paper_id, rank,
       snippet(chunk_fts, 3, '<b>', '</b>', '...', 32) AS snippet
FROM chunk_fts
WHERE chunk_fts MATCH ?
ORDER BY rank
LIMIT 60    -- fetch more; group by paper, keep top 3 per paper
```

Group results by `paper_id`, keep the 3 highest-ranked chunks per paper. Attach
snippets from the FTS5 `snippet()` function.

Snippets from `.txt`-sourced chunks are tagged with `location_confidence: "low"` (read
from the `chunks` table via `chunk_id` join).

---

## Edge cases

- FTS5 MATCH error from malformed query string: catch `sqlite3.OperationalError` and
  return `[]` with a logged warning. Do not propagate the error to the user.
- `--field title`: query `paper_fts` with column filter on `title` only; disable
  `chunk_fts` query.
- `--field authors`: similar, `authors` column only; no chunk search.
- `--field summary`: query `summary_short` and `summary_technical` columns; enable
  chunk search.
- `--field all` (default): query all columns; enable chunk search.
- No results from either paper_fts or chunk_fts: return `[]`.
- FTS5 returns `rank = 0.0` for some rows: treat as lowest rank.

---

## Tests required

`tests/test_fts.py` (new):
- Exact title match returns the correct paper.
- Author match returns the correct paper.
- Chunk hit returns a snippet and correct `paper_id`.
- No-result case returns `[]`.
- Physics term with `/` in it (e.g. `Al/InAs`) does not raise an FTS5 parse error.
- `--field title` restricts results to title matches only.

---

## Acceptance criteria

- [ ] `keyword_search(query, conn, aliases, n=20) -> list[FtsHit]` exists in
  `search/fts.py`.
- [ ] FTS5 query built through a safe query builder — no raw user strings passed to MATCH.
- [ ] Physics chars (`-`, `/`, `:`, `(`, `±`) escaped or quoted without collapsing
  multi-word expansions into single phrases.
- [ ] Chunk hits grouped by paper; top 3 snippets per paper.
- [ ] `snippet()` used for excerpt generation.
- [ ] `FtsHit` dataclass in `search/models.py`.
