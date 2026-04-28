from paperlib.handle import generate_handle_id
from paperlib.models.metadata import MetadataField
from paperlib.models.record import PaperRecord


def _record(
    *,
    paper_id: str = "p_0440c911081cc43b",
    authors=None,
    year=None,
) -> PaperRecord:
    record = PaperRecord(paper_id=paper_id)
    record.metadata["authors"] = MetadataField(value=authors)
    record.metadata["year"] = MetadataField(value=year)
    return record


def test_generate_handle_id_uses_first_author_surname_and_year():
    record = _record(authors=["C. G. L. Bøttcher"], year=2024)

    assert generate_handle_id(record, set()) == "bottcher_2024"


def test_generate_handle_id_uses_untitled_when_author_missing():
    record = _record(authors=None, year=2024)

    assert generate_handle_id(record, set()) == "untitled_2024"


def test_generate_handle_id_uses_hash_when_author_and_year_missing():
    record = _record(authors=None, year=None)

    assert generate_handle_id(record, set()) == "paper_0440c911"


def test_generate_handle_id_skips_a_suffix_for_collisions():
    record = _record(authors=["C. G. L. Bøttcher"], year=2024)

    first = generate_handle_id(record, set())
    second = generate_handle_id(record, {first})
    third = generate_handle_id(record, {first, second})

    assert first == "bottcher_2024"
    assert second == "bottcher_2024_b"
    assert third == "bottcher_2024_c"
