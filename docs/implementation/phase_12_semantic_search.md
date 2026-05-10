# Phase 12 — Semantic Search

Full spec: [`v1_3_plan.md § Phase 12`](../../v1_3_plan.md)

## Goal

Dense vector similarity search over paper-level and chunk-level embeddings. Used in
`--mode semantic` and as a component of hybrid ranking.

## Prerequisites

- Phase 5 — `paper_embeddings` and `chunk_embeddings` tables must exist.
- Phase 9 — embeddings must be precomputed and stored.
- Phase 8 — normalization and alias expansion applied to the query before embedding.

## Files to modify

| File | Action |
|---|---|
| `src/paperlib/search/embeddings.py` | Add `semantic_search()` to the file created in Phase 9 |
| `src/paperlib/search/models.py` | Add `SemanticHit`, `ChunkHit` dataclasses |

---

## Implementation

### Result types — `search/models.py`

```python
@dataclass
class ChunkHit:
    chunk_id: str
    section_title: str | None
    page_start: int | None
    page_end: int | None
    source_type: str
    location_confidence: str
    score: float
    snippet: str | None      # short excerpt from chunk text

@dataclass
class SemanticHit:
    paper_id: str
    paper_score: float
    chunk_hits: list[ChunkHit]   # top-3 chunks for this paper
```

### `semantic_search` — `search/embeddings.py`

```python
def semantic_search(
    query: str, conn, config, aliases: dict[str, list[str]], *, n: int = 20
) -> list[SemanticHit]:
```

Steps:

1. Normalize query via `normalize_query(query)`.
2. Expand aliases via `expand_query(normalized, aliases)`.
3. Embed the expanded query text using the same `SentenceTransformer` model and
   `normalize_embeddings=True`. Query vector is float32, dim=384.
4. Load `paper_embeddings` from the DB. **Skip any row** where `source_hash`, `model`,
   or `dimension` does not match the current embedding config values. All three must
   agree — a model or dimension mismatch means the vector is incompatible even if the
   source text is unchanged. Stale vectors must not be used.
5. Compute dot product (= cosine, since vectors are normalized) between query vector
   and each paper vector. Use `numpy`.
6. Take top `n` papers by score.
7. Load `chunk_embeddings` for those `n` papers. Apply the same staleness check
   (compare against current `chunks.text_hash` for source_hash, and configured model /
   dimension).
8. For each of the top-n papers, compute dot product between query vector and chunk
   vectors. Take top-3 chunks per paper.
9. Fetch snippet text from the `chunks` table for top chunk hits (short excerpt; first
   ~200 chars of chunk text, or implement a simple windowed excerpt).
10. Return `list[SemanticHit]`.

### Missing embeddings

**`--mode semantic`** (pure semantic):
If no paper embeddings are found after the staleness filter:
```
Error: no embeddings found. Run `paperlib embed` first, or use --mode keyword.
```
Exit non-zero.

**Hybrid mode** (Phase 14 orchestrates this):
If embeddings are missing, skip the semantic component and emit a single warning line:
```
Warning: no embeddings found; semantic component skipped. Run `paperlib embed` to enable.
```
Keyword and fuzzy results are still returned. Hybrid search degrades gracefully.

### Staleness check detail

For `paper_embeddings`:
```python
current_model = config.search.embedding_model
current_dim = 384   # known for all-MiniLM-L6-v2; could also be derived from model
row["model"] == current_model and row["dimension"] == current_dim
# source_hash: not re-checked at query time for paper embeddings
# (source_hash is used at embed time to skip re-embedding; at query time
#  the model+dimension check is sufficient to detect incompatible vectors)
```

Wait — the plan says "skip any row where source_hash, model, AND dimension does not
match". At query time, you may not have the source text readily available to recompute
source_hash. Clarification: at **query time**, skip rows where `model` or `dimension`
doesn't match current config. The `source_hash` check at query time protects against
using vectors from a different version of the text — load the source_hash from the
stored row and compare against a freshly computed hash for the current record text. If
the record has changed since embedding (e.g. summary updated), the source_hash will
differ and the vector is stale.

---

## Edge cases

- No embeddings at all in the DB: semantic mode exits non-zero; hybrid mode warns and
  continues.
- Some papers have embeddings, some don't (partial state): use the ones available.
  Papers without embeddings are not returned in semantic results (they simply have no
  vector to score).
- Dimension mismatch (model changed): all stored vectors fail the dimension check and
  are skipped. This looks like "no embeddings" — the same error/warning path applies.
- Chunks with `location_confidence = "low"` (`.txt` source): return them with
  `page_start = None`, `page_end = None` — clearly labeled.

---

## Tests required

`tests/test_semantic.py` (new):
- Use mock embeddings (pre-computed cosine similarity math, no model load).
- Top-n papers returned correctly by dot product score.
- Top-3 chunks per paper.
- Stale vectors (model mismatch) skipped.
- No embeddings → appropriate error/warning depending on mode.

`tests/test_search_degraded.py` (new — also covers Phase 14):
- Hybrid mode with no embeddings: emits warning, returns keyword + fuzzy results.
- Semantic mode with no embeddings: exits non-zero.

---

## Acceptance criteria

- [ ] `semantic_search(query, conn, config, aliases, n=20) -> list[SemanticHit]` in
  `search/embeddings.py`.
- [ ] Query embedded with same model and `normalize_embeddings=True`.
- [ ] Stale vectors (model or dimension mismatch) skipped at query time.
- [ ] Top-3 chunks per paper in results.
- [ ] `--mode semantic` exits non-zero with actionable message if no valid embeddings.
- [ ] `SemanticHit` and `ChunkHit` dataclasses in `search/models.py`.
- [ ] Chunks labeled with `location_confidence` and `page_start`/`page_end`.
