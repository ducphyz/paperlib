# Phase 9 — Embeddings

Full spec: [`v1_3_plan.md § Phase 9`](../../v1_3_plan.md)

## Goal

Precompute and store dense vectors for semantic search. Provide incremental update
semantics so unchanged text is not re-embedded.

## Prerequisites

- Phase 5 — `paper_embeddings` and `chunk_embeddings` tables must exist.
- Phase 6 — `chunks` table must be populated (embed runs after `rebuild-search-index`).
- Phase 8 — normalization applied to query before embedding at search time.

## Files to create or modify

| File | Action |
|---|---|
| `src/paperlib/search/embeddings.py` | Create — `embed_papers()`, `embed_chunks()`, storage/load helpers |
| `src/paperlib/cli.py` | Add `embed` command |

---

## Implementation

### Backend

v1.3 uses only the local `sentence-transformers` backend:

```toml
[search]
embedding_backend = "local"
embedding_model   = "sentence-transformers/all-MiniLM-L6-v2"
```

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
vectors = model.encode(texts, normalize_embeddings=True)
```

- `normalize_embeddings=True` — vectors are L2-normalized; dot product equals cosine.
- Store as `float32` BLOBs: `vector.astype("float32").tobytes()`.
- Expected dimension: 384.
- First run downloads the model from Hugging Face (~90 MB). Subsequent runs are offline.

If `sentence-transformers` is not installed:
```
Error: sentence-transformers is not installed. Reinstall paperlib: pip install -e .
```
Note: it is a required dependency — this error should not occur in a normal install.

### Paper embedding source text

Concatenate, joining with ` | `:
```
title | authors | summary.short | summary.technical | tags |
materials | devices | phenomena | quantities | aliases | methods
```

All from the JSON record. Missing/None fields are skipped.

### Chunk embedding source text

```python
f"{section_title}: {chunk_text}" if section_title else chunk_text
```

The section title prefix improves chunk retrieval recall.

### `source_hash`

```python
import hashlib
source_hash = hashlib.sha256(source_text.encode()).hexdigest()
```

### Incremental skip

Skip re-embed when **all three** of `source_hash`, `model`, and `dimension` match the
stored values. Any mismatch → re-embed. This ensures that switching embedding models or
any model that produces a different vector dimension always invalidates stored vectors.

```python
def _should_skip(conn, paper_id, source_hash, model, dimension) -> bool:
    row = conn.execute(
        "SELECT source_hash, model, dimension FROM paper_embeddings WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    if row is None:
        return False
    return (
        row["source_hash"] == source_hash
        and row["model"] == model
        and row["dimension"] == dimension
    )
```

Same pattern for `chunk_embeddings` (keyed on `chunk_id`).

### `embed_papers` — `search/embeddings.py`

```python
def embed_papers(
    config, conn, records: list[PaperRecord], *, force: bool = False
):
    """Yields ProgressEvent objects."""
```

For each record:
1. Build source text.
2. Compute `source_hash`.
3. If not `force` and `_should_skip(...)`: continue.
4. Yield `ProgressKind.EMBED_START`.
5. Embed via `SentenceTransformer.encode([source_text], normalize_embeddings=True)[0]`.
6. Store:
   ```sql
   INSERT OR REPLACE INTO paper_embeddings
       (paper_id, model, dimension, vector, source_hash, created_at)
   VALUES (?, ?, ?, ?, ?, datetime('now'));
   ```
7. Yield `ProgressKind.EMBED_DONE`.

### `embed_chunks` — `search/embeddings.py`

```python
def embed_chunks(
    config, conn, chunks: list[Chunk], *, force: bool = False
):
    """Yields ProgressEvent objects."""
```

Same pattern. Source text: `f"{section_title}: {text}"` or just `text`.

Yield `ProgressKind.CHUNK_EMBED_START` / `ProgressKind.CHUNK_EMBED_DONE`.

### CLI — `cli.py`

```
paperlib embed                   # embed all missing
paperlib embed --force           # re-embed all (ignore source_hash match)
paperlib embed --backend local   # override config (future-proofing; only local in v1.3)
paperlib embed --dry-run         # report missing/stale counts; skip model load and DB writes
```

`paperlib embed` requires chunks to exist. If the `chunks` table is empty:
```
Error: no chunks found. Run `paperlib rebuild-search-index` first.
```

`--dry-run`: count how many papers/chunks lack embeddings or have stale ones. Report
counts. Do not load the embedding model. Make no DB writes.

`paperlib rebuild-search-index --embeddings` and `paperlib embed` use the same
underlying functions. The difference: `rebuild-search-index` always rebuilds chunks and
FTS first; `embed` skips chunk/FTS. Running both is safe but redundant.

---

## Edge cases

- Model dimension mismatch (e.g. user switches model from 384-dim to 768-dim): any
  stored vector with a different `dimension` is stale and will be re-embedded. The
  `dimension` field on each stored row enables this check without loading the model.
- `source_hash` unchanged but `model` changed: re-embed (model mismatch).
- Batch encoding: encode all missing/stale texts in a single `model.encode()` call
  per batch for efficiency. Do not call `encode()` once per chunk.
- `embed --dry-run` must not load `SentenceTransformer` — the model download (~90 MB)
  would violate dry-run semantics.

---

## Tests required

`tests/test_embeddings_invalidation.py` (new):
- Changing `embedding_model` config causes re-embed (source_hash unchanged, model
  changed → mismatch).
- Changing dimension (different model) also invalidates.
- Both paper and chunk embeddings tested.
- Unchanged source_hash + model + dimension → skip.

`tests/test_semantic.py` (new — Phase 12 may share this file):
- Use mock embeddings (cosine sim math only, no model load) to test the retrieval logic.

---

## Acceptance criteria

- [ ] `embed_papers()` and `embed_chunks()` yield `ProgressEvent` objects; no `print`.
- [ ] Incremental skip: re-embed only when `source_hash` OR `model` OR `dimension`
  differ from stored values.
- [ ] Vectors stored as `float32` BLOBs with `dimension = 384`.
- [ ] `normalize_embeddings=True` used in all `encode()` calls.
- [ ] `embed --dry-run` reports counts and makes no DB writes and does not load model.
- [ ] `embed` exits with actionable message if `chunks` table is empty.
- [ ] `sentence-transformers` is a required dep; no optional `[search]` extra.
