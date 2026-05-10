# Phase 7 — Rebuild Search Index

Full spec: [`v1_3_plan.md § Phase 7`](../../v1_3_plan.md)

## Goal

Single command that (re)builds the chunks table, FTS5 tables, and optionally embeddings.
Acts as the orchestrator that coordinates the chunker, FTS population, and embedding
calls.

## Prerequisites

- Phase 5 — v3 schema tables must exist.
- Phase 6 — `chunk_document()` must be implemented.
- Phase 8 — normalization/aliases used during the index build for consistency (can be
  stubbed initially).
- Phase 9 — embedding functions called by `--embeddings` flag (can be added after
  initial index works).

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/search/index.py` | Create — orchestrator, `ProgressEvent`, `ProgressKind` |
| `src/paperlib/cli.py` | Add `rebuild-search-index` command |

---

## Implementation

### `ProgressKind` and `ProgressEvent` — `search/index.py`

```python
from enum import StrEnum
from dataclasses import dataclass

class ProgressKind(StrEnum):
    PAPER_START       = "paper_start"
    PAPER_DONE        = "paper_done"
    CHUNKS_WRITTEN    = "chunks_written"
    FTS_REBUILT       = "fts_rebuilt"
    EMBED_START       = "embed_start"
    EMBED_DONE        = "embed_done"
    CHUNK_EMBED_START = "chunk_embed_start"
    CHUNK_EMBED_DONE  = "chunk_embed_done"
    ORPHAN_CLEANED    = "orphan_cleaned"

@dataclass(frozen=True)
class ProgressEvent:
    kind:      ProgressKind
    paper_id:  str | None = None
    handle_id: str | None = None
    count:     int | None = None
    total:     int | None = None
    status:    str | None = None
```

**No `print` or `click.echo` anywhere in `index.py`.** All user-facing output is in
`cli.py`, which consumes the yielded events.

### `rebuild_search_index` — `search/index.py`

```python
def rebuild_search_index(
    config,
    *,
    embeddings: bool = False,
    force: bool = False,
    dry_run: bool = False,
):
    """Yields ProgressEvent objects."""
```

#### Orchestration steps

1. **Collect `current_paper_ids`** by scanning all JSON record files in
   `config.paths.records`. Load each record via `store/json_store.py`.

2. **If `force`:** delete all `paper_embeddings` and `chunk_embeddings` before the main
   loop. (Chunk+FTS are always fully rebuilt regardless of `--force`.)

3. **One transaction** covering all papers:

   a. **Delete stale chunks:**
      - If `current_paper_ids` is non-empty:
        ```sql
        DELETE FROM chunks WHERE paper_id NOT IN (current_paper_ids);
        ```
      - If `current_paper_ids` is empty:
        ```sql
        DELETE FROM chunks;
        ```
      Both paths then proceed to the per-paper loop (which runs zero times for empty
      library).

   b. **Per-paper loop:**
      ```sql
      DELETE FROM chunks WHERE paper_id = ?;
      ```
      Then compute chunks via `chunk_document(record, config)` and insert:
      ```sql
      INSERT INTO chunks (chunk_id, paper_id, file_hash, source_type,
          location_confidence, section_title, page_start, page_end,
          chunk_order, text, text_hash, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
      ```
      Yield `ProgressKind.CHUNKS_WRITTEN` with `count=len(chunks)`.

   c. **Rebuild `chunk_fts` once** after all chunk inserts:
      ```sql
      INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild');
      ```
      One rebuild per run — not per paper. Yield `ProgressKind.FTS_REBUILT`.

4. **Populate `paper_fts`:**
   - Delete stale rows:
     - Non-empty: `DELETE FROM paper_fts WHERE paper_id NOT IN (current_paper_ids)`
     - Empty: `DELETE FROM paper_fts`
   - Insert/replace for all current papers from JSON records (not from the `papers`
     SQL table). Fields: `paper_id`, `title`, `authors`, `year`, `tags`,
     `summary_short`, `summary_technical`, `materials`, `devices`, `phenomena`,
     `quantities`, `aliases`, `methods`, `key_contributions`.

5. **Unconditional orphan cleanup** — always runs, not gated on `--embeddings`:
   ```sql
   DELETE FROM chunk_embeddings
   WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)
      OR paper_id NOT IN (SELECT paper_id FROM papers);
   ```
   Yield `ProgressKind.ORPHAN_CLEANED` with `count` of rows deleted.

6. **Upsert `search_index_state`** (id=1):
   ```sql
   INSERT OR REPLACE INTO search_index_state (id, built_at, record_count, chunk_count)
   VALUES (1, datetime('now'), ?, ?);
   ```
   `record_count` = len of current JSON set. `chunk_count` = total rows in `chunks`.

7. **If `--embeddings`:** call the embedding functions (Phase 9) for each paper and
   chunk that lacks a current embedding (or all if `--force` was also passed).

#### `--force` semantics

| Flags | Chunks + FTS | Embeddings |
|---|---|---|
| _(none)_ | Fully rebuilt | Preserved; orphans cleaned |
| `--embeddings` | Fully rebuilt | Rebuilt for missing/stale; orphans cleaned |
| `--force` | Fully rebuilt | Wiped entirely; no re-embed |
| `--force --embeddings` | Fully rebuilt | Wiped then fully re-embedded |

"Fully rebuilt" always means all current papers — there is no incremental skip for
chunks or FTS in v1.3.

### CLI — `cli.py`

```
paperlib rebuild-search-index               # chunks + FTS only
paperlib rebuild-search-index --embeddings  # + embeddings
paperlib rebuild-search-index --force       # clear and rebuild all
paperlib rebuild-search-index --dry-run     # compute chunk counts, skip all DB writes
```

`--dry-run`: scan records, compute chunk counts, yield events, but write nothing to DB.

CLI consumes each `ProgressEvent` and formats it:
- `PAPER_START` → `Processing {handle_id or paper_id}...`
- `CHUNKS_WRITTEN` → `  {count} chunks`
- `FTS_REBUILT` → `FTS index rebuilt.`
- `ORPHAN_CLEANED` → `Cleaned {count} orphaned chunk embeddings.`

---

## Edge cases

- Empty library (zero JSON records): `current_paper_ids = []`. Use `DELETE FROM chunks`
  (not `IN ()`). The per-paper loop runs zero times. FTS rebuild still runs to produce
  an empty but consistent index. `search_index_state` is upserted with
  `record_count=0`, `chunk_count=0`.
- SQLite `IN ()` with an empty list is a syntax error — always use the `DELETE FROM
  chunks` path when `current_paper_ids` is empty.
- JSON record load failures: skip the record, log a warning, do not abort the run.
- `chunk_fts` rebuild must happen inside the same transaction as the chunk inserts.
  Do not commit between chunk inserts and the FTS rebuild.

---

## Tests required

`tests/test_index_rebuild.py` (new):
- Idempotency: run `rebuild_search_index` twice, assert same chunk and FTS state.
- Regression: `paperlib rebuild-index` (the existing command) succeeds when v3 search
  tables already exist.
- `chunk_fts` is empty and consistent after `rebuild-index` clears base tables.
- `search_index_state` has a row with correct `record_count` and `chunk_count` after
  `rebuild-search-index`.

`tests/test_stale_chunks.py` (new):
- Delete a JSON record, run `rebuild-search-index`, assert chunks and `chunk_embeddings`
  for that `paper_id` are removed.
- `search_index_state.record_count` reflects the new count.

`tests/test_progress_events.py` (new):
- `index.py` yields only `ProgressEvent` objects.
- `kind` values are members of `ProgressKind`.
- No `print` or `click.echo` calls in `index.py` (static check or mock).

---

## Acceptance criteria

- [ ] `rebuild_search_index` yields `ProgressEvent` objects only; no `print`.
- [ ] `ProgressKind` and `ProgressEvent` defined with all specified fields.
- [ ] Chunks fully rebuilt for all current papers (no incremental skip).
- [ ] `chunk_fts` rebuilt once per run, inside the same transaction as chunk inserts.
- [ ] Stale chunks deleted at start of rebuild (`paper_id NOT IN current_ids`).
- [ ] Empty-library edge case handled without `IN ()` SQL error.
- [ ] Orphan `chunk_embeddings` cleaned unconditionally (by `chunk_id` AND `paper_id`).
- [ ] `search_index_state` upserted after every successful rebuild.
- [ ] `--force` wipes embeddings before rebuild; `--force --embeddings` wipes then
  re-embeds.
- [ ] `--dry-run` makes no DB writes.
- [ ] `paperlib rebuild-search-index` CLI command present with all flags.
