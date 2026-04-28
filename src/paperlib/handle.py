from __future__ import annotations

from typing import Any

from paperlib.store.fs import filename_author_component, sanitize_component


_MAX_HANDLE_BASE_LEN = 40


def generate_handle_id(record, existing_handles: set[str]) -> str:
    base = _handle_base(record)
    if base not in existing_handles:
        return base

    suffix_number = 2
    while True:
        suffix = _suffix_for_number(suffix_number)
        candidate_base = base[: _MAX_HANDLE_BASE_LEN - len(suffix) - 1]
        candidate_base = candidate_base.rstrip("_-") or "paper"
        candidate = f"{candidate_base}_{suffix}"
        if candidate not in existing_handles:
            return candidate
        suffix_number += 1


def _handle_base(record) -> str:
    paper_id = _paper_id(record)
    hash8 = _hash8(paper_id)
    year = _field_value(record, "year")
    author = _first_author(record)
    author_component = filename_author_component(author)

    if author_component and year is not None:
        base = f"{author_component}_{year}"
    elif author_component:
        base = f"{author_component}_{hash8}"
    elif year is not None:
        base = f"untitled_{year}"
    else:
        base = f"paper_{hash8}"

    return _trim_base(sanitize_component(str(base)))


def _first_author(record) -> str | None:
    authors = _field_value(record, "authors")
    if not isinstance(authors, list) or not authors:
        return None
    first = authors[0]
    if not isinstance(first, str) or not first.strip():
        return None
    return first.strip()


def _field_value(record, field_name: str) -> Any:
    metadata = _record_get(record, "metadata", {})
    if not isinstance(metadata, dict):
        return None
    field = metadata.get(field_name)
    if hasattr(field, "value"):
        return field.value
    if isinstance(field, dict):
        return field.get("value")
    return None


def _paper_id(record) -> str:
    value = _record_get(record, "paper_id", "")
    return value if isinstance(value, str) else ""


def _record_get(record, key: str, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _hash8(paper_id: str) -> str:
    value = paper_id.removeprefix("p_")[:8]
    value = sanitize_component(value)
    return value or "unknown"


def _trim_base(base: str) -> str:
    base = base[:_MAX_HANDLE_BASE_LEN].rstrip("_-")
    return base or "paper"


def _suffix_for_number(number: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    index = number - 1
    chars = []
    while True:
        chars.append(alphabet[index % 26])
        index = index // 26 - 1
        if index < 0:
            break
    return "".join(reversed(chars))
