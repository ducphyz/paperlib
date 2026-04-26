import json
from pathlib import Path

import pytest

from paperlib.models.record import PaperRecord
from paperlib.store.json_store import (
    JsonStoreError,
    read_record,
    read_record_dict,
    write_record_atomic,
)


def test_write_and_read_record_round_trip(tmp_path: Path):
    path = tmp_path / "records" / "p_test.json"
    record = PaperRecord(paper_id="p_test")

    write_record_atomic(path, record)

    loaded = read_record(path)
    assert loaded.paper_id == "p_test"
    assert loaded.schema_version == 1


def test_write_and_read_dict_round_trip(tmp_path: Path):
    path = tmp_path / "records" / "p_test.json"
    record = PaperRecord(paper_id="p_test").to_dict()

    write_record_atomic(path, record)

    loaded = read_record_dict(path)
    assert loaded["paper_id"] == "p_test"
    assert loaded["schema_version"] == 1


def test_invalid_schema_version_raises(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

    with pytest.raises(JsonStoreError):
        read_record_dict(path)


def test_missing_schema_version_raises(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"paper_id": "p_test"}), encoding="utf-8")

    with pytest.raises(JsonStoreError):
        read_record(path)


def test_successful_write_leaves_final_file_without_tmp(tmp_path: Path):
    path = tmp_path / "records" / "p_test.json"

    write_record_atomic(path, PaperRecord(paper_id="p_test"))

    assert path.exists()
    assert not path.with_name(f"{path.name}.tmp").exists()


def test_written_json_is_parseable(tmp_path: Path):
    path = tmp_path / "records" / "p_test.json"

    write_record_atomic(path, PaperRecord(paper_id="p_test"))

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    assert data["paper_id"] == "p_test"
