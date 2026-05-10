# Phase 8 — Normalization and Aliases

Full spec: [`v1_3_plan.md § Phase 8`](../../v1_3_plan.md)

## Goal

Deterministic query normalization and domain alias expansion. Applied to queries at
search time; not stored in the index.

## Prerequisites

- None — this is standalone utility code. Can be implemented in parallel with Phases 6–7.

## Files to create

| File | Action |
|---|---|
| `src/paperlib/search/normalize.py` | Create — `normalize_query()` |
| `src/paperlib/search/aliases.py` | Create — `load_aliases()`, `expand_query()` |
| `src/paperlib/search/data/__init__.py` | Create (empty) |
| `src/paperlib/search/data/search_aliases.toml` | Create — bundled alias file |

---

## Implementation

### `normalize_query` — `search/normalize.py`

```python
def normalize_query(text: str) -> str:
```

Applies in this order:
1. Lowercase.
2. Unicode dash normalization → ASCII hyphen (`‐`–`―`, `−` → `-`).
3. Collapse whitespace (multiple spaces, tabs, newlines → single space).
4. Strip leading and trailing punctuation from the whole string.
5. Simple heuristic plural/singular — suffix-level only, not full lemmatization:
   - Strip trailing `s` if the result is ≥ 4 characters and doesn't end in `ss`.
   - Example: `resonators` → `resonator`, `lass` stays `lass`.

No stopword removal in normalization — stopwords are stripped separately in the ranking
phase (Phase 13) for the multi-concept bonus.

### `load_aliases` — `search/aliases.py`

Load the bundled TOML alias file via `importlib.resources`:

```python
import importlib.resources
import tomllib

def load_aliases(alias_file: str = "") -> dict[str, list[str]]:
    """
    Returns dict mapping abbreviation → list of expanded forms.
    alias_file: empty string → use bundled default; non-empty → load from that path.
    """
    if alias_file:
        with open(alias_file, "rb") as f:
            data = tomllib.load(f)
    else:
        ref = importlib.resources.files("paperlib.search.data").joinpath(
            "search_aliases.toml"
        )
        with importlib.resources.as_file(ref) as path:
            with open(path, "rb") as f:
                data = tomllib.load(f)
    return data.get("aliases", {})
```

`alias_file` comes from `config.search.alias_file` (Phase 15). Empty string means use
bundled file.

### `expand_query` — `search/aliases.py`

```python
def expand_query(
    normalized_query: str, aliases: dict[str, list[str]]
) -> tuple[str, list[str]]:
    """
    Returns (expanded_query, expanded_terms).
    expanded_terms: list of (original, expansion) pairs for --json output.
    """
```

For each token in the normalized query that exactly matches a key in `aliases`, append
the expanded forms. The expanded query is the original query plus the additional terms.
Expansion is applied to query tokens only — not stored in the index.

Include expanded terms in `--json` output so queries are transparent.

### Bundled alias file — `search/data/search_aliases.toml`

```toml
[aliases]
cpw       = ["coplanar waveguide", "coplanar waveguide resonator", "CPW resonator"]
soc       = ["spin-orbit coupling", "spin orbit coupling", "spin-orbit interaction"]
alinas    = ["Al-InAs", "Al/InAs", "AlInAs"]
sf_hybrid = ["superconductor-ferromagnet", "S/F hybrid", "superconductor ferromagnet bilayer"]
jj        = ["Josephson junction", "tunnel junction"]
vna       = ["vector network analyzer", "VNA"]
2deg      = ["two-dimensional electron gas", "2DEG"]
qd        = ["quantum dot"]
sc        = ["superconductor", "superconducting"]
```

Add more as needed. Keys are normalized (lowercase). Values are a list of canonical
expansion strings — may include mixed case for proper display.

### `pyproject.toml` changes

Required for the bundled alias file to be included in the installed package:

```toml
[tool.setuptools.package-data]
"paperlib.search.data" = ["*.toml"]
```

`search/data/` must contain `__init__.py` for setuptools to treat it as a package.

---

## Edge cases

- `alias_file` path does not exist: raise `ConfigError` immediately at load time, not at
  search time.
- Alias key matches a substring of a token (e.g. query is `"soc-driven"`, key is `"soc"`):
  match whole tokens only, not substrings.
- Circular expansion (alias key appears in its own expansion list): ignore. Do not
  recursively expand.
- Query with no matching alias keys: `expanded_terms = []`, `expanded_query == normalized_query`.

---

## Tests required

`tests/test_normalize.py` (new):
- Lowercase, hyphen normalization (Unicode → ASCII), whitespace collapse, punctuation
  stripping.
- Plural heuristic: `resonators` → `resonator`; `glass` stays `glass`.

`tests/test_aliases.py` (new):
- Bundled aliases load via `importlib.resources` without a file path.
- `config.search.alias_file` override loads from a user-provided TOML file instead of
  the bundled default.
- `cpw` in query expands to coplanar waveguide forms.
- Non-matching query token → no expansion.
- Whole-token matching only.

---

## Acceptance criteria

- [ ] `normalize_query` applies lowercase, dash normalization, whitespace collapse,
  punctuation strip, plural heuristic in that order.
- [ ] `load_aliases("")` loads the bundled `search_aliases.toml` via
  `importlib.resources`.
- [ ] `load_aliases(path)` loads from a user-provided file path.
- [ ] `[tool.setuptools.package-data]` includes `"paperlib.search.data" = ["*.toml"]`.
- [ ] `search/data/__init__.py` exists.
- [ ] Alias expansion matches whole tokens only.
- [ ] Expanded terms included in search output (consumed by Phase 14 for display).
