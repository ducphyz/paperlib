from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from paperlib.models import status as status_values
from paperlib.models.identity import normalize_arxiv_id, normalize_doi
from paperlib.models.record import PaperRecord


InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


class ReviewCancelled(Exception):
    pass


@dataclass
class ReviewChange:
    label: str
    old_value: object
    new_value: object


def review_record_interactive(
    record: PaperRecord,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    now: str | None = None,
) -> PaperRecord | None:
    updated = deepcopy(record)
    now = now or _utc_now()
    changes: list[ReviewChange] = []

    if _is_already_reviewed(updated):
        if not _confirm(
            "Record is already reviewed/locked. Continue editing? [y/N] ",
            input_func,
            output_func,
            default=False,
        ):
            output_func("No changes written.")
            return None

    output_func(f"Reviewing {updated.handle_id or updated.paper_id}")
    for field_name in ("title", "authors", "year", "journal"):
        if field_name not in updated.metadata:
            continue
        changes.extend(
            _review_metadata_field(
                updated,
                field_name,
                input_func=input_func,
                output_func=output_func,
                now=now,
            )
        )

    changes.extend(
        _review_identity_field(
            updated,
            "doi",
            input_func=input_func,
            output_func=output_func,
        )
    )
    changes.extend(
        _review_identity_field(
            updated,
            "arxiv_id",
            input_func=input_func,
            output_func=output_func,
        )
    )
    changes.extend(
        _review_notes(
            updated,
            input_func=input_func,
            output_func=output_func,
        )
    )

    mark_whole_record = _confirm(
        "Mark whole record as reviewed and locked? [y/N] ",
        input_func,
        output_func,
        default=False,
    )
    if mark_whole_record:
        old_status = updated.status.get("review")
        old_locked = updated.review.get("locked", False)
        updated.status["review"] = status_values.REVIEW_REVIEWED
        updated.review["locked"] = True
        updated.review["reviewed_at"] = now
        changes.append(
            ReviewChange(
                "record review",
                {"status": old_status, "locked": old_locked},
                {"status": status_values.REVIEW_REVIEWED, "locked": True},
            )
        )

    if changes:
        updated.timestamps["updated_at"] = now
        output_func("Summary of changes:")
        for change in changes:
            output_func(
                f"- {change.label}: {_format_value(change.old_value)} -> "
                f"{_format_value(change.new_value)}"
            )
    else:
        output_func("No changes selected.")

    if not _confirm(
        "Save review changes? [y/N] ",
        input_func,
        output_func,
        default=False,
    ):
        output_func("Review not saved.")
        return None

    if _identity_changed(changes):
        _refresh_identity_aliases(updated)
    return updated


def _review_metadata_field(
    record: PaperRecord,
    field_name: str,
    *,
    input_func: InputFunc,
    output_func: OutputFunc,
    now: str,
) -> list[ReviewChange]:
    field = record.metadata[field_name]
    output_func(
        f"{field_name}: {_format_value(field.value)} "
        f"(source={field.source or '<none>'}, "
        f"confidence={_format_confidence(field.confidence)})"
    )

    while True:
        raw_value = _prompt(
            f"New {field_name} (blank keep, ! lock): ",
            input_func,
        )
        if raw_value == "":
            return []
        if raw_value == "!":
            if field.locked:
                return []
            old_value = field.value
            field.locked = True
            field.updated_at = now
            return [ReviewChange(field_name, old_value, old_value)]

        try:
            value = _parse_metadata_value(field_name, raw_value)
        except ValueError as exc:
            output_func(str(exc))
            continue

        old_value = field.value
        field.value = value
        field.source = status_values.SOURCE_USER
        field.confidence = 1.0
        field.locked = True
        field.updated_at = now
        return [ReviewChange(field_name, old_value, value)]


def _review_identity_field(
    record: PaperRecord,
    field_name: str,
    *,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> list[ReviewChange]:
    old_value = getattr(record.identity, field_name)
    output_func(f"{field_name}: {_format_value(old_value)}")
    raw_value = _prompt(f"New {field_name} (blank keep): ", input_func)
    if raw_value == "":
        return []

    if field_name == "doi":
        new_value = normalize_doi(raw_value)
    elif field_name == "arxiv_id":
        new_value = normalize_arxiv_id(raw_value)
    else:
        new_value = raw_value.strip() or None

    if new_value == old_value:
        return []
    setattr(record.identity, field_name, new_value)
    return [ReviewChange(field_name, old_value, new_value)]


def _review_notes(
    record: PaperRecord,
    *,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> list[ReviewChange]:
    old_value = record.review.get("notes", "")
    output_func(f"review notes: {_format_value(old_value)}")
    raw_value = _prompt("New review notes (blank keep): ", input_func)
    if raw_value == "":
        return []
    record.review["notes"] = raw_value
    return [ReviewChange("review notes", old_value, raw_value)]


def _parse_metadata_value(field_name: str, raw_value: str):
    value = raw_value.strip()
    if field_name == "authors":
        return [part.strip() for part in value.split(",") if part.strip()]
    if field_name == "year":
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("Invalid year; enter an integer year.") from exc
    return value


def _confirm(
    prompt: str,
    input_func: InputFunc,
    output_func: OutputFunc,
    *,
    default: bool,
) -> bool:
    while True:
        value = _prompt(prompt, input_func).strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        output_func("Please answer y or n.")


def _prompt(prompt: str, input_func: InputFunc) -> str:
    try:
        return input_func(prompt).strip()
    except (KeyboardInterrupt, EOFError) as exc:
        raise ReviewCancelled("Review cancelled; no changes written.") from exc


def _is_already_reviewed(record: PaperRecord) -> bool:
    return (
        record.status.get("review") == status_values.REVIEW_REVIEWED
        or record.review.get("locked", False)
    )


def _identity_changed(changes: list[ReviewChange]) -> bool:
    return any(change.label in {"doi", "arxiv_id"} for change in changes)


def _refresh_identity_aliases(record: PaperRecord) -> None:
    aliases = [
        alias
        for alias in record.identity.aliases
        if not alias.startswith(("doi:", "arxiv:"))
    ]
    if record.identity.arxiv_id:
        aliases.append(f"arxiv:{record.identity.arxiv_id}")
    if record.identity.doi:
        aliases.append(f"doi:{record.identity.doi}")

    deduplicated = []
    for alias in aliases:
        if alias not in deduplicated:
            deduplicated.append(alias)
    record.identity.aliases = deduplicated


def _format_value(value) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "<none>"
    if value == "":
        return "<empty>"
    return str(value)


def _format_confidence(value) -> str:
    return "<none>" if value is None else str(value)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
