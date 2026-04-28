from paperlib.models.record import PaperRecord


def test_paper_record_from_dict_loads_old_json_without_handle_id():
    record = PaperRecord.from_dict({"schema_version": 1, "paper_id": "p_old"})

    assert record.paper_id == "p_old"
    assert record.handle_id is None


def test_paper_record_to_dict_includes_handle_id_when_set():
    record = PaperRecord(paper_id="p_test", handle_id="smith_2024")

    data = record.to_dict()

    assert list(data)[:3] == ["schema_version", "paper_id", "handle_id"]
    assert data["handle_id"] == "smith_2024"


def test_paper_record_to_dict_includes_null_handle_id_when_missing():
    data = PaperRecord(paper_id="p_test").to_dict()

    assert data["handle_id"] is None
