# Phase 16 — Tests

Full spec: [`v1_3_plan.md § Phase 16`](../../v1_3_plan.md)

## Goal

Comprehensive test coverage for all v1.3 phases. This document is a reference checklist;
the tests themselves should be written alongside or immediately after the code they cover.

## Prerequisites

All implementation phases should be complete or in progress before filling gaps in this
checklist.

## Fixtures

Create 5–8 synthetic paper records in `tests/fixtures/` with matching `.md` and `.json`
files. Cover these physics domains for predictable search hits:

| Handle | Domain |
|---|---|
| `smith_sc_fm_2022` | Superconductor-ferromagnet hybrid |
| `jones_cpw_2021` | CPW resonator |
| `chen_soc_2020` | Spin-orbit coupling |
| `lee_alinas_2023` | AlInAs heterostructure |
| `wang_qd_2019` | Quantum dot transport |

Fixture `.md` files must:
- Use canonical `<!-- page: N -->` markers (monotonically increasing from 1).
- Use `##` section headings.
- Include a `## References` section.
- Have predictable text to produce deterministic FTS and fuzzy hits.

Fixture `.json` records must:
- Have `summary.physics` with `materials`, `devices`, `phenomena`, `quantities`,
  `aliases` populated.
- Have `metadata.title.value`, `metadata.authors.value`, `metadata.year.value` set.

---

## Test file checklist

### Existing files to extend

| File | New tests |
|---|---|
| `tests/test_record.py` | `_default_summary()["physics"]` has `phenomena`, `quantities`, `aliases`; `FileRecord` with `markdown` round-trips |
| `tests/test_config.py` | `SearchConfig`, extended `ExtractionConfig`, `markdown_model` validation, `AppConfig.search` |
| `tests/test_ai_client.py` | `call_ai_with_pdf` routing, Anthropic provider → `AIError` |
| `tests/test_db.py` | `delete_paper` with v3 FK children; `search_index_state` untouched |

### New files

| File | Covers | Phase |
|---|---|---|
| `tests/test_validate_markdown.py` | All validation checks, refusal detection, monotonic markers, partial vs. failed | 3 |
| `tests/test_extract_md.py` | Orchestrator skip logic, dry-run, failed → `MarkdownInfo(status="failed")`, page_count override | 2 |
| `tests/test_chunking.py` | Chunk count, section headers, page markers, source_type/confidence, file selection logic | 6 |
| `tests/test_normalize.py` | Hyphen variants, whitespace collapse, plural heuristic | 8 |
| `tests/test_aliases.py` | Bundled load via `importlib.resources`, user override, whole-token matching | 8 |
| `tests/test_fts.py` | Exact title, author, chunk hit, no-result, physics chars, `--field` restriction | 10 |
| `tests/test_fuzzy.py` | Typo cases (resnator, feromagnetic, bogolubov), threshold, `--field` | 11 |
| `tests/test_semantic.py` | Mock embeddings, top-n papers, top-3 chunks, stale skipping | 12 |
| `tests/test_ranking.py` | Weight formula, multi-concept bonus, score cap, why list | 13 |
| `tests/test_index_rebuild.py` | Idempotency, rebuild-index regression, chunk_fts consistency, search_index_state | 7 |
| `tests/test_stale_chunks.py` | Delete record → stale chunks and embeddings removed; record_count updated | 7 |
| `tests/test_progress_events.py` | Only `ProgressEvent` yielded; kind is `ProgressKind`; no print/click.echo | 7 |
| `tests/test_search_json.py` | JSON schema validation, expanded_terms, score_breakdown, relevant_chunks | 14 |
| `tests/test_search_degraded.py` | Hybrid with no embeddings warns; semantic exits non-zero; missing state row; empty library OK | 12, 14 |
| `tests/test_embeddings_invalidation.py` | Model change → re-embed; dimension change → re-embed; match → skip | 9 |

---

## Key scenarios to test

### Validation (Phase 3)
- Empty / None / near-empty → `"failed"`.
- Refusal in first 1000 chars → `"failed"`.
- Refusal after 1000 chars (quoted text) → NOT failed.
- No page markers → `"failed"`.
- Wrong marker format (`Page 1`) → `"partial"`.
- Non-monotonic markers → `"partial"`.
- First marker ≠ 1 → `"partial"`.
- Missing references for 10+ page paper → `"partial"`.
- All checks pass → `"validated"`.

### Chunking (Phase 6)
- File selection: `source_file_hash` target with `word_count = 0` falls through to sort.
- File selection: `equation_heavy` file with words beats `scanned` file.
- Chunk IDs are identical across two calls with same source text + config.
- `.txt` source: `page_start = None`, `page_end = None`.

### Index rebuild (Phase 7)
- Run twice: same chunk count, same chunk_ids (idempotency).
- Delete a JSON record, rebuild: chunks for that paper gone.
- `rebuild-index` (old command) does not error when v3 tables exist.
- Empty library: `search_index_state.record_count = 0`; `chunk_count = 0`; no SQL error.

### Search degraded (Phases 12, 14)
- No `search_index_state` row → `"search index not built"` error.
- Empty `paper_fts` + valid state row → NOT an error.
- No embeddings + `--mode semantic` → non-zero exit.
- No embeddings + `--mode hybrid` → warning, keyword+fuzzy returned, exit 0.

### Embeddings invalidation (Phase 9)
- `source_hash` unchanged, `model` changed → re-embed.
- `source_hash` changed, `model` unchanged → re-embed.
- All three match → skip.

### Config (Phase 15)
- Unprefixed `markdown_model` → `ConfigError`.
- `anthropic:` prefix → `ConfigError`.
- `openai-compat:` prefix → accepted.
- `[search]` section absent → defaults applied, no error.

---

## Acceptance criteria

- [ ] All listed test files exist and are non-empty.
- [ ] `pytest` passes with all new tests.
- [ ] No new test uses `print` or `click.echo` inside library modules under test
  (assert via mocking or by checking event types).
- [ ] Fixture `.md` files use canonical `<!-- page: N -->` markers and `##` headings.
- [ ] Both paper and chunk embeddings covered in `test_embeddings_invalidation.py`.
- [ ] `test_search_degraded.py` covers: no state row, empty-library state row, no
  embeddings semantic, no embeddings hybrid.
