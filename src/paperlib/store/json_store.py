from __future__ import annotations

import json
import os
from pathlib import Path

from paperlib.models.record import PaperRecord


SCHEMA_VERSION = 1


class JsonStoreError(Exception):
    pass


def write_record_atomic(path: Path, record: PaperRecord | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = record.to_dict() if isinstance(record, PaperRecord) else record
    temp_path = path.with_name(f"{path.name}.tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_record(path: Path) -> PaperRecord:
    return PaperRecord.from_dict(read_record_dict(path))


def read_record_dict(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    _validate_schema_version(data)
    return data


def _validate_schema_version(data: dict) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise JsonStoreError(
            f"Unsupported schema_version: {data.get('schema_version')!r}"
        )
