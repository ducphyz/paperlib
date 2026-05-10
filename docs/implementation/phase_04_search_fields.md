# Phase 4 — Search Field Augmentation (Optional)

Full spec: [`v1_3_plan.md § Phase 4`](../../v1_3_plan.md)

## Goal

Add `phenomena`, `quantities`, and `aliases` to the AI summarisation prompt so they are
populated in existing records. FTS and semantic search work without this phase; it
improves recall for domain-specific queries.

**This phase is not on the critical path for v1.3.** Defer or skip if it risks the
milestone.

## Prerequisites

- Phase 1 — the three fields must already exist in `_default_summary()["physics"]` (that
  change is required in Phase 1 regardless of whether Phase 4 is done).

## Files to modify

| File | Action |
|---|---|
| `src/paperlib/ai/prompts.py` | Update summary prompt to elicit `phenomena`, `quantities`, `aliases` |
| `src/paperlib/cli.py` | Add `--search-fields` flag to `re-summarise` command |

---

## Implementation

### Prompt update — `ai/prompts.py`

Extend the existing summarisation prompt to request the three new fields as part of the
`physics` block:

```json
"phenomena": ["induced superconductivity", "spin-orbit coupling"],
"quantities": ["quality factor", "superfluid density"],
"aliases": ["CPW", "JJ"]
```

`aliases` here refers to AI-assigned or user-assigned abbreviations specific to the
paper, not to the global search alias file in `search/data/search_aliases.toml`.

The prompt must make clear that:
- `phenomena` = physical effects or mechanisms observed or studied.
- `quantities` = measurable physical quantities (not methodology).
- `aliases` = abbreviations or alternate names used in this paper specifically.

### `re-summarise --search-fields` — `cli.py`

Add a `--search-fields` flag to the existing `re-summarise` command. When set:
- Only regenerate `summary.physics.phenomena`, `summary.physics.quantities`,
  `summary.physics.aliases` for records that have these fields empty.
- Do not regenerate `summary.short`, `summary.technical`, `key_contributions`, etc.
- Respect `summary.locked = true` — skip locked summaries.
- Respect `--dry-run`.

This allows backfilling only the new fields on existing records without triggering a
full AI re-summarisation (and its API cost).

---

## Edge cases

- Records with `summary.locked = true` are skipped entirely.
- Records with `summary.status != "ok"` should also be skipped (can't backfill partial).
- If the AI returns `aliases = []` for a paper, that is valid — not every paper defines
  abbreviations.
- `phenomena`, `quantities`, `aliases` are lists; ensure the AI returns lists, not
  strings. Validate before writing.

---

## Tests required

No new test files required for Phase 4 unless `re-summarise --search-fields` is
implemented. If implemented:

`tests/test_resummary.py` (extend existing):
- `--search-fields` populates only the three new physics fields.
- Locked summaries are not modified.

---

## Acceptance criteria

- [ ] Summarisation prompt elicits `phenomena`, `quantities`, `aliases` from the AI.
- [ ] `re-summarise --search-fields` backfills only the three new fields (if implemented).
- [ ] Locked summaries are unchanged.
- [ ] Existing test suite passes.
