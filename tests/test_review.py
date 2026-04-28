from pathlib import Path

import pytest
from click.testing import CliRunner

from paperlib.cli import main
from paperlib.models import status
from paperlib.models.record import PaperRecord
from paperlib.review import ReviewCancelled, review_record_interactive
from paperlib.store import db
from paperlib.store.json_store import read_record, write_record_atomic


def _inputs(values):
    iterator = iter(values)

    def input_func(_prompt: str) -> str:
        return next(iterator)

    return input_func


def _review_inputs(
    *,
    title="",
    authors="",
    year="",
    journal="",
    doi="",
    arxiv_id="",
    notes="",
    mark_record="n",
    save="y",
):
    return [
        title,
        authors,
        year,
        journal,
        doi,
        arxiv_id,
        notes,
        mark_record,
        save,
    ]


def _record() -> PaperRecord:
    record = PaperRecord(paper_id="p_review", handle_id="review_2024")
    record.identity.doi = "10.1234/old"
    record.identity.aliases = [
        "hash:abcdef1234567890",
        "doi:10.1234/old",
    ]
    record.metadata["title"].value = "Old Title"
    record.metadata["title"].source = "ai"
    record.metadata["title"].confidence = 0.7
    record.metadata["authors"].value = ["Old Author"]
    record.metadata["year"].value = 2024
    record.metadata["journal"].value = "Old Journal"
    return record


def _write_config(path: Path, root: Path) -> None:
    path.write_text(
        f"""
[library]
root = "{root}"

[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
duplicates = "duplicates"

[pipeline]
move_after_ingest = true
skip_existing = true
dry_run_default = false

[extraction]
engine = "pdfplumber"
min_char_count = 500
min_word_count = 100

[ai]
enabled = false
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2
""",
        encoding="utf-8",
    )


def _write_record_fixture(root: Path, record: PaperRecord) -> Path:
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    record_path = records_dir / f"{record.paper_id}.json"
    write_record_atomic(record_path, record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, f"records/{record.paper_id}.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
    finally:
        conn.close()
    return record_path


def test_review_blank_keeps_existing_values():
    record = _record()

    updated = review_record_interactive(
        record,
        input_func=_inputs(_review_inputs()),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated is not None
    assert updated.metadata["title"].value == "Old Title"
    assert updated.metadata["authors"].value == ["Old Author"]
    assert updated.metadata["year"].value == 2024
    assert updated.metadata["journal"].value == "Old Journal"
    assert record.timestamps["updated_at"] is None


def test_review_value_overwrites_and_locks_metadata_field():
    updated = review_record_interactive(
        _record(),
        input_func=_inputs(_review_inputs(title="New Title")),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    field = updated.metadata["title"]
    assert field.value == "New Title"
    assert field.source == status.SOURCE_USER
    assert field.confidence == 1.0
    assert field.locked is True
    assert field.updated_at == "2026-04-28T00:00:00Z"
    assert updated.timestamps["updated_at"] == "2026-04-28T00:00:00Z"


def test_review_authors_input_is_stored_as_list():
    updated = review_record_interactive(
        _record(),
        input_func=_inputs(
            _review_inputs(authors="Alice Smith, Bob Jones")
        ),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated.metadata["authors"].value == ["Alice Smith", "Bob Jones"]
    assert updated.metadata["authors"].source == status.SOURCE_USER
    assert updated.metadata["authors"].locked is True


def test_review_lock_only_keeps_value_and_locks_field():
    updated = review_record_interactive(
        _record(),
        input_func=_inputs(_review_inputs(title="!")),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated.metadata["title"].value == "Old Title"
    assert updated.metadata["title"].locked is True
    assert updated.metadata["title"].source == "ai"


def test_review_mark_whole_record_sets_reviewed_and_locked():
    updated = review_record_interactive(
        _record(),
        input_func=_inputs(_review_inputs(mark_record="y")),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated.status["review"] == status.REVIEW_REVIEWED
    assert updated.review["locked"] is True
    assert updated.review["reviewed_at"] == "2026-04-28T00:00:00Z"


def test_already_reviewed_prompt_no_exits_without_changes():
    record = _record()
    record.status["review"] = status.REVIEW_REVIEWED

    updated = review_record_interactive(
        record,
        input_func=_inputs(["n"]),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated is None
    assert record.metadata["title"].value == "Old Title"


def test_already_reviewed_prompt_yes_allows_edits():
    record = _record()
    record.review["locked"] = True

    updated = review_record_interactive(
        record,
        input_func=_inputs(["y", *_review_inputs(title="Reviewed Title")]),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated.metadata["title"].value == "Reviewed Title"
    assert updated.metadata["title"].locked is True


def test_review_cancel_writes_nothing_to_input_record():
    record = _record()

    def raise_interrupt(_prompt: str) -> str:
        raise KeyboardInterrupt

    with pytest.raises(ReviewCancelled):
        review_record_interactive(
            record,
            input_func=raise_interrupt,
            output_func=lambda _line: None,
            now="2026-04-28T00:00:00Z",
        )

    assert record.metadata["title"].value == "Old Title"
    assert record.metadata["title"].locked is False


def test_review_decline_confirmation_returns_none_without_mutating_input():
    record = _record()

    updated = review_record_interactive(
        record,
        input_func=_inputs(_review_inputs(title="New Title", save="n")),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated is None
    assert record.metadata["title"].value == "Old Title"
    assert record.metadata["title"].locked is False


def test_review_invalid_year_reprompts():
    updated = review_record_interactive(
        _record(),
        input_func=_inputs(
            [
                "",
                "",
                "twenty",
                "2026",
                "",
                "",
                "",
                "",
                "n",
                "y",
            ]
        ),
        output_func=lambda _line: None,
        now="2026-04-28T00:00:00Z",
    )

    assert updated.metadata["year"].value == 2026
    assert updated.metadata["year"].source == status.SOURCE_USER


def test_cli_review_by_handle_updates_json_and_sqlite(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    record_path = _write_record_fixture(root, _record())

    result = CliRunner().invoke(
        main,
        ["review", "review_2024", "--config", str(config_path)],
        input="CLI Title\n\n\n\n\n\n\nn\ny\n",
    )

    assert result.exit_code == 0
    assert "review saved: review_2024" in result.output
    assert not record_path.with_name(f"{record_path.name}.tmp").exists()
    record = read_record(record_path)
    assert record.metadata["title"].value == "CLI Title"
    assert record.metadata["title"].source == status.SOURCE_USER
    assert record.metadata["title"].confidence == 1.0
    assert record.metadata["title"].locked is True

    conn = db.connect(root / "db" / "library.db")
    try:
        row = conn.execute(
            "SELECT title, review_status FROM papers WHERE paper_id = ?",
            (record.paper_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["title"] == "CLI Title"
    assert row["review_status"] == status.REVIEW_NEEDS_REVIEW


def test_cli_review_by_paper_id_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    record_path = _write_record_fixture(root, _record())

    result = CliRunner().invoke(
        main,
        ["review", "p_review", "--config", str(config_path)],
        input="Paper ID Title\n\n\n\n\n\n\nn\ny\n",
    )

    assert result.exit_code == 0
    assert read_record(record_path).metadata["title"].value == "Paper ID Title"


def test_cli_review_identity_change_refreshes_db_aliases(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _write_record_fixture(root, _record())

    result = CliRunner().invoke(
        main,
        ["review", "review_2024", "--config", str(config_path)],
        input="\n\n\n\nhttps://doi.org/10.5555/NewDOI\n\n\nn\ny\n",
    )

    assert result.exit_code == 0
    conn = db.connect(root / "db" / "library.db")
    try:
        assert db.resolve_id(conn, "doi:10.5555/newdoi") == "p_review"
        with pytest.raises(db.IdNotFound):
            db.resolve_id(conn, "doi:10.1234/old")
    finally:
        conn.close()


def test_cli_review_cancel_writes_no_json_or_sqlite(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    record_path = _write_record_fixture(root, _record())
    before_json = record_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["review", "review_2024", "--config", str(config_path)],
        input="\x03",
    )

    assert result.exit_code != 0
    assert "Review cancelled; no changes written." in result.output
    assert record_path.read_text(encoding="utf-8") == before_json
    conn = db.connect(root / "db" / "library.db")
    try:
        row = conn.execute(
            "SELECT title FROM papers WHERE paper_id = 'p_review'"
        ).fetchone()
    finally:
        conn.close()
    assert row["title"] == "Old Title"


def test_cli_review_decline_confirmation_writes_nothing(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    record_path = _write_record_fixture(root, _record())
    before_json = record_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["review", "review_2024", "--config", str(config_path)],
        input="Unsaved Title\n\n\n\n\n\n\nn\nn\n",
    )

    assert result.exit_code == 0
    assert "Review not saved." in result.output
    assert record_path.read_text(encoding="utf-8") == before_json
