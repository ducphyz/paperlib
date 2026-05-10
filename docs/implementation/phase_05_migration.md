# Phase 5 — SQLite Migration v3

Full spec: [`v1_3_plan.md § Phase 5`](../../v1_3_plan.md)

## Goal

Extend `db/library.db` with chunk, FTS, embedding, and state tables. Update the two
deletion paths (`_clear_index_tables` and `delete_paper`) to maintain FK integrity.

## Prerequisites

None — this is foundational and can be done before or alongside Phases 1–3.

## Files to modify

| File | Action |
|---|---|
| `src/paperlib/store/migrations.py` | Bump `SCHEMA_VERSION = 3`; add `_migrate_to_v3()` |
| `src/paperlib/store/db.py` | Update `_clear_index_tables`; update `delete_paper` |

---

## Implementation

### `SCHEMA_VERSION` and `apply_migrations` — `store/migrations.py`

```python
SCHEMA_VERSION = 3  # was 2
```

In `apply_migrations`, after the existing `_migrate_to_v2(conn)` call:

```python
_migrate_to_v3(conn)
conn.execute(
    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
    (SCHEMA_VERSION,),
)
conn.commit()
```

### `_migrate_to_v3` — `store/migrations.py`

```python
def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id                  INTEGER PRIMARY KEY,
            chunk_id            TEXT UNIQUE NOT NULL,
            paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
            file_hash           TEXT NOT NULL,
            source_type         TEXT NOT NULL,
            location_confidence TEXT NOT NULL,
            section_title       TEXT,
            page_start          INTEGER,
            page_end            INTEGER,
            chunk_order         INTEGER NOT NULL,
            text                TEXT NOT NULL,
            text_hash           TEXT NOT NULL,
            created_at          TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5 (
            paper_id UNINDEXED,
            title,
            authors,
            year,
            tags,
            summary_short,
            summary_technical,
            materials,
            devices,
            phenomena,
            quantities,
            aliases,
            methods,
            key_contributions
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5 (
            chunk_id UNINDEXED,
            paper_id UNINDEXED,
            section_title,
            text,
            content='chunks',
            content_rowid='id'
        );

        CREATE TABLE IF NOT EXISTS paper_embeddings (
            paper_id     TEXT PRIMARY KEY REFERENCES papers(paper_id),
            model        TEXT NOT NULL,
            dimension    INTEGER NOT NULL,
            vector       BLOB NOT NULL,
            source_hash  TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_id     TEXT PRIMARY KEY,
            paper_id     TEXT NOT NULL,
            model        TEXT NOT NULL,
            dimension    INTEGER NOT NULL,
            vector       BLOB NOT NULL,
            source_hash  TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS search_index_state (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            built_at      TEXT NOT NULL,
            record_count  INTEGER NOT NULL,
            chunk_count   INTEGER NOT NULL
        );
    """)
```

**Notes on table design:**

- `chunks.id` is an `INTEGER PRIMARY KEY` (required for FTS5 external content
  `content_rowid`).
- `chunk_fts` is external-content FTS5 pointing to `chunks`. Do NOT `DELETE FROM
  chunk_fts` directly — use `INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')` to
  resync after chunk changes.
- `chunk_embeddings` has **no FK to `chunks`**. A FK would prevent `--force` rebuilds
  that DELETE+INSERT chunks while preserving embeddings. Orphan cleanup is handled
  explicitly in `index.py`.
- `paper_fts` is a standalone FTS5 table (not external-content). It is populated
  explicitly by `rebuild-search-index` from JSON records, not via triggers on `papers`.
  This avoids schema coupling and FTS corruption risk.
- `search_index_state` allows at most one row (`CHECK (id = 1)`). Use `INSERT OR
  REPLACE` to upsert.

### `_clear_index_tables` — `store/db.py`

Replace the existing implementation with one that clears v3 search artifacts before
base tables (FK constraint order):

```python
def _clear_index_tables(conn: sqlite3.Connection) -> None:
    # v3 search artifacts — cleared before base tables due to FK constraints.
    # paper_fts is standalone FTS5: plain DELETE works.
    # chunk_fts is external-content FTS5: do NOT DELETE FROM chunk_fts.
    # After clearing chunks, run INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')
    # to produce an empty, internally consistent FTS index.
    # Both must run inside the same transaction as the table clears.
    for table in ("paper_fts", "chunk_embeddings", "paper_embeddings", "chunks",
                  "search_index_state"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
    # Base tables
    for table in ("processing_runs", "aliases", "files", "papers"):
        conn.execute(f"DELETE FROM {table}")
```

After `rebuild-index` runs this, `chunk_fts` is empty and consistent, and
`search_index_state` has no row — so `paperlib search` correctly reports "index not
built".

### `delete_paper` — `store/db.py`

Update to clear v3 FK children before deleting from `papers`:

```python
def delete_paper(conn: sqlite3.Connection, paper_id: str) -> None:
    try:
        conn.execute("BEGIN")
        # v3 search artifacts (FK children of papers)
        conn.execute(
            "DELETE FROM chunk_embeddings WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM paper_embeddings WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM chunks WHERE paper_id = ?", (paper_id,)
        )
        conn.execute(
            "DELETE FROM paper_fts WHERE paper_id = ?", (paper_id,)
        )
        # Base tables
        conn.execute(
            "DELETE FROM processing_runs WHERE paper_id = ?", (paper_id,)
        )
        conn.execute("DELETE FROM aliases WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM files WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

**Why `chunk_fts` is not explicitly deleted here:** `chunk_fts` is an external-content
FTS5 table that tracks `chunks`. Deleting from `chunks` leaves orphaned FTS entries, but
they are harmless until the next `rebuild-search-index` resyncs the table with
`INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')`. Adding per-paper FTS cleanup to
`delete_paper` would require an expensive per-paper FTS delete, which is unnecessary
for a batch resync pattern.

`search_index_state` is not per-paper and is left untouched by `delete_paper`.

---

## Edge cases

- `_migrate_to_v3` uses `CREATE TABLE IF NOT EXISTS` / `CREATE VIRTUAL TABLE IF NOT
  EXISTS` so it is safe to run on a database that was already partially migrated.
- `_clear_index_tables` is always called inside an existing `BEGIN` transaction in
  `rebuild_index_from_records` — the `chunk_fts` rebuild must be part of the same
  transaction.
- Databases created before v1.3 that do not have the new tables yet: `apply_migrations`
  runs `_migrate_to_v3` at startup, which creates them idempotently.

---

## Tests required

`tests/test_index_rebuild.py` (new):
- **Regression:** `paperlib rebuild-index` succeeds when v3 search tables and embeddings
  already exist. This exercises the FK-safe `_clear_index_tables` order.
- After `rebuild-index`, `chunk_fts` is empty and consistent (not stale).
- After `rebuild-index`, `search_index_state` has no row.

`tests/test_db.py` (extend existing):
- `delete_paper` succeeds when the paper has chunks and embeddings (v3 FK children
  exist).
- `delete_paper` leaves `search_index_state` untouched.

---

## Acceptance criteria

- [ ] `SCHEMA_VERSION = 3` in `migrations.py`.
- [ ] `_migrate_to_v3` creates all six new tables idempotently.
- [ ] `chunk_embeddings` has no FK to `chunks`.
- [ ] `search_index_state` enforces `CHECK (id = 1)`.
- [ ] `_clear_index_tables` deletes v3 tables first, runs `chunk_fts` rebuild, then
  clears base tables — all inside the same transaction.
- [ ] `delete_paper` clears `chunk_embeddings`, `paper_embeddings`, `chunks`,
  `paper_fts` before deleting from `papers`.
- [ ] Existing test suite passes (migration is additive; no existing tables changed).
