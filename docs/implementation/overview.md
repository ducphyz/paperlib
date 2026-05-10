# v1.3 Implementation Overview

Full spec: [`v1_3_plan.md`](../../v1_3_plan.md)

---

## Phase index

| Doc | Phase | Description |
|---|---|---|
| [phase_01_models.md](phase_01_models.md) | 1 | Data models — `MarkdownInfo`, `MarkdownExtractionResult`, `MarkdownExtractor` Protocol, physics fields |
| [phase_02_extraction.md](phase_02_extraction.md) | 2 | PDF → Markdown extraction — `call_ai_with_pdf`, `openai_pdf` provider, orchestrator, `extract-markdown` CLI |
| [phase_03_validation.md](phase_03_validation.md) | 3 | Markdown validation — `validate_markdown()`, status rules |
| [phase_04_search_fields.md](phase_04_search_fields.md) | 4 | Search field augmentation (**optional**) — prompt updates, `re-summarise --search-fields` |
| [phase_05_migration.md](phase_05_migration.md) | 5 | SQLite migration v3 — new tables, `_clear_index_tables`, `delete_paper` |
| [phase_06_chunking.md](phase_06_chunking.md) | 6 | Chunking — file selection, source priority, chunk ID stability, `chunk_document()` |
| [phase_07_rebuild_index.md](phase_07_rebuild_index.md) | 7 | Rebuild search index — `index.py` orchestrator, `rebuild-search-index` CLI, `ProgressEvent` |
| [phase_08_normalize_aliases.md](phase_08_normalize_aliases.md) | 8 | Normalization and aliases — `normalize.py`, `aliases.py`, bundled `search_aliases.toml` |
| [phase_09_embeddings.md](phase_09_embeddings.md) | 9 | Embeddings — `embed_texts()`, storage, incremental skip, `embed` CLI |
| [phase_10_keyword_search.md](phase_10_keyword_search.md) | 10 | Keyword search — FTS5 query builder, `keyword_search()`, snippet generation |
| [phase_11_fuzzy_search.md](phase_11_fuzzy_search.md) | 11 | Fuzzy search — RapidFuzz over metadata fields, `fuzzy_search()` |
| [phase_12_semantic_search.md](phase_12_semantic_search.md) | 12 | Semantic search — `semantic_search()`, stale-vector skipping, degraded-mode handling |
| [phase_13_ranking.md](phase_13_ranking.md) | 13 | Hybrid ranking — score formula, multi-concept bonus, why-matched explanations |
| [phase_14_search_cli.md](phase_14_search_cli.md) | 14 | Search CLI — `service.py`, extended `search` command, JSON output, empty-index guard |
| [phase_15_config.md](phase_15_config.md) | 15 | Config additions — `SearchConfig`, `ExtractionConfig` markdown fields, `AppConfig.search` |
| [phase_16_tests.md](phase_16_tests.md) | 16 | Tests — fixtures, full test file list, coverage checklist |

---

## Recommended implementation order

The plan numbers phases logically, not strictly sequentially. Implement in this order to
avoid blocked work:

```
Phase 15 → Phase 1 → Phase 5 → Phase 6 → Phase 8 → Phase 7
                              ↓                          ↓
                        Phase 2+3               Phase 9 → 10 → 11 → 12 → 13 → 14
```

**Phase 15 (config) first** — `SearchConfig` and `ExtractionConfig` markdown fields are
referenced by almost every subsequent phase. Do it before or alongside Phase 1.

**Phase 4 is optional** — skip if it risks the milestone. FTS and semantic search work
without the new prompt fields.

---

## Cross-cutting invariants

These apply to every phase. Do not break them.

1. **Library modules never print.** All `click.echo` calls live in `cli.py` only.
   `search/index.py` and `search/embeddings.py` yield `ProgressEvent` objects; CLI
   consumes and formats them.
2. **JSON is truth.** `records/{paper_id}.json` wins over SQLite on any conflict.
3. **Atomic writes only.** Use `store/fs.py` temp-file → `fsync` → rename. No bare
   `open(..., 'w')` for library data files.
4. **SQLite writes use transactions.** No bare writes outside a transaction.
5. **v3 FK delete order.** Any deletion path that removes a `papers` row must first
   delete `chunk_embeddings`, `paper_embeddings`, `chunks`, and `paper_fts` for that
   `paper_id`. Wrong order raises a constraint error (`PRAGMA foreign_keys = ON`).
6. **`--dry-run` is strictly read-only.** No file moves, no JSON/text writes, no SQLite
   updates, no AI calls, no model loads.
7. **AI never overwrites locked fields.** Respect `locked: true` on any metadata field
   or summary.
8. **`paper_id` is permanent.** Never reassigned after first ingest.
9. **Embeddings are rebuildable.** Skip re-embed only when `source_hash`, `model`, AND
   `dimension` all match. Any mismatch → re-embed.

---

## New runtime dependencies

All three are required (no optional `[search]` extra):

| Package | Purpose |
|---|---|
| `rapidfuzz` | Fuzzy string matching (Phase 11) |
| `numpy` | Vector math for embeddings (Phase 9) |
| `sentence-transformers` | Local embedding model, ~90 MB first-run download (Phase 9) |

Add to `pyproject.toml` `dependencies` list. Also add:

```toml
[tool.setuptools.package-data]
"paperlib.search.data" = ["*.toml"]
```

---

## New source directories

```
src/paperlib/
  pipeline/
    markdown_extractors/    # Phase 1, 2, 3
      __init__.py
      base.py
      openai_pdf.py
      validate.py
    extract_md.py           # Phase 2
  search/                   # Phases 6–14
    __init__.py
    models.py
    normalize.py
    aliases.py
    chunking.py
    index.py
    fts.py
    fuzzy.py
    embeddings.py
    ranking.py
    service.py
    data/
      __init__.py
      search_aliases.toml
```
