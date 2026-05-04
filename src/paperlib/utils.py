from __future__ import annotations

from datetime import UTC, datetime

from paperlib.models import status as status_values
from paperlib.models.record import PaperRecord


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def field_exists(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def metadata_status(record: PaperRecord) -> str:
    title_exists = field_exists(record.metadata["title"].value)
    authors_exist = field_exists(record.metadata["authors"].value)
    if title_exists and authors_exist:
        return status_values.METADATA_OK
    if any([
        title_exists,
        authors_exist,
        field_exists(record.metadata["journal"].value),
        field_exists(record.metadata["year"].value),
        field_exists(record.identity.doi),
        field_exists(record.identity.arxiv_id),
    ]):
        return status_values.METADATA_PARTIAL
    return status_values.METADATA_PENDING
