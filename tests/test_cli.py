from pathlib import Path
import json
import tomllib

import pytest
from click.testing import CliRunner

from conftest import (
    _insert_list_row,
    _relative_files,
    _write_config,
    _write_minimal_pdf,
    _write_show_fixture,
)
from paperlib.__about__ import __version__
from paperlib.cli import main
from paperlib.models.file import FileRecord
from paperlib.models.record import PaperRecord
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.ingest import IngestReport
from paperlib.pipeline.validate import ValidationResult
from paperlib.store import db
from paperlib.store.json_store import read_record, write_record_atomic


CLI_COMMANDS = (
    "validate-config",
    "validate-library",
    "ingest",
    "status",
    "list",
    "show",
    "delete",
    "rebuild-index",
    "mark-reviewed",
    "review",
)

LIST_HEADER = (
    f"{'handle_id':<20}  {'paper_id':<18}  {'year':<9}  "
    f"{'first_author':<20}  {'title':<57}  {'review_status':<12}"
)
LIST_HEADER_NO_HANDLE = (
    f"{'paper_id':<18}  {'year':<9}  {'first_author':<20}  "
    f"{'title':<57}  {'review_status':<12}"
)


def _expected_list_row(
    *,
    paper_id: str,
    year: str,
    first_author: str,
    title: str,
    review_status: str,
    handle_id: str | None = None,
) -> str:
    values = [
        f"{paper_id:<18}",
        f"{year:<9}",
        f"{first_author:<20}",
        f"{title:<57}",
        f"{review_status:<12}",
    ]
    if handle_id is not None:
        values.insert(0, f"{handle_id:<20}")
    return "  ".join(values)


def _write_delete_fixture(root: Path, config_path: Path) -> dict:
    _write_config(config_path, root)
    records_dir = root / "records"
    pdf_path = root / "papers" / "2024" / "delete.pdf"
    text_path = root / "text" / "delete.txt"
    records_dir.mkdir(parents=True)
    pdf_path.parent.mkdir(parents=True)
    text_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF delete fixture")
    text_path.write_text("delete fixture text", encoding="utf-8")

    record = PaperRecord(paper_id="p_delete", handle_id="delete_2024")
    record.identity.doi = "10.1234/delete"
    record.identity.arxiv_id = "2401.99999"
    record.identity.aliases = [
        "doi:10.1234/delete",
        "arxiv:2401.99999",
        "hash:dddddddddddddddd",
    ]
    record.metadata["title"].value = "Delete Me"
    record.metadata["authors"].value = ["Ada"]
    record.metadata["year"].value = 2024
    file_record = FileRecord(
        file_hash="d" * 64,
        original_filename="delete.pdf",
        canonical_path="papers/2024/delete.pdf",
        text_path="text/delete.txt",
        size_bytes=pdf_path.stat().st_size,
        added_at="2026-04-26T00:00:00Z",
    )
    record.files.append(file_record)
    record_path = records_dir / "p_delete.json"
    write_record_atomic(record_path, record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_delete.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
        db.insert_file(conn, record.paper_id, file_record)
        db.log_processing_run(
            conn,
            file_record.file_hash,
            record.paper_id,
            "ingest",
            "ok",
            None,
        )
    finally:
        conn.close()

    return {
        "record": record,
        "record_path": record_path,
        "pdf_path": pdf_path,
        "text_path": text_path,
        "deleted_pdf_path": root / "deleted" / "delete.pdf",
    }


def test_root_version_prints_project_version():
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert "paperlib" in result.output
    assert __version__ in result.output


def test_about_version_matches_pyproject():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert __version__ == data["project"]["version"]


def test_root_help_lists_description_and_commands():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "Usage: paperlib" in result.output
    assert "Personal CLI tool" in result.output
    for command in CLI_COMMANDS:
        assert command in result.output


@pytest.mark.parametrize("command", CLI_COMMANDS)
def test_each_command_help_exits_zero(command: str):
    result = CliRunner().invoke(main, [command, "--help"])

    assert result.exit_code == 0, command
    assert "--config" in result.output


def test_ingest_help_documents_core_options():
    result = CliRunner().invoke(main, ["ingest", "--help"])

    assert result.exit_code == 0
    for option in ("--dry-run", "--no-ai", "--limit", "--debug"):
        assert option in result.output


def test_global_config_help_is_available():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--config" in result.output


def test_global_config_ingest_form_works(tmp_path: Path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "custom.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((config.library.root, limit, dry_run, no_ai))
        return IngestReport(discovered=1, processed=1)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(config_path),
            "ingest",
            "--no-ai",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert calls == [(root.resolve(), 1, False, True)]


def test_per_command_config_ingest_form_still_works(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "custom.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((config.library.root, limit, dry_run, no_ai))
        return IngestReport(discovered=1, processed=1)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        [
            "ingest",
            "--config",
            str(config_path),
            "--no-ai",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert calls == [(root.resolve(), 1, False, True)]


def test_rebuild_index_help_documents_core_options():
    result = CliRunner().invoke(main, ["rebuild-index", "--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "--no-backfill" in result.output


def test_list_help_documents_existing_options():
    result = CliRunner().invoke(main, ["list", "--help"])

    assert result.exit_code == 0
    assert "--needs-review" in result.output
    assert "--no-handle" in result.output
    assert "--sort" in result.output


def test_ingest_dry_run_prints_table_and_forces_no_ai(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    discovered = DiscoveredPDF(
        path=root / "inbox" / "paper.pdf",
        file_hash="a" * 64,
        hash16="a" * 16,
        hash8="a" * 8,
        size_bytes=2048,
        modified_time="2026-04-25T12:34:56Z",
    )
    calls = []

    monkeypatch.setattr(
        "paperlib.cli.discover_pdfs",
        lambda inbox_path: [discovered],
    )
    monkeypatch.setattr(
        "paperlib.cli.validate_pdf",
        lambda path: ValidationResult(path, True, 3, True, "ok"),
    )

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(discovered=1, processed=1)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        ["ingest", "--dry-run", "--limit", "1", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "path | hash16 | size (KB) | pages | validation | reason" in result.output
    assert (
        f"{discovered.path} | {discovered.hash16} | 2 | 3 | ok | ok"
        in result.output
    )
    assert calls == [(1, True, True)]


def test_validate_config_succeeds_when_root_exists(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["validate-config", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert f"Library root: {root}" in result.output


def test_validate_config_fails_when_root_missing(tmp_path: Path):
    root = tmp_path / "missing-library"
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["validate-config", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "Library root does not exist" in result.output


def test_cli_ingest_dry_run_writes_nothing_with_synthetic_pdf(tmp_path: Path):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    pdf_path = inbox / "paper.pdf"
    _write_minimal_pdf(pdf_path)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["ingest", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert pdf_path.exists()
    assert not (root / "records").exists()
    assert not (root / "text").exists()
    assert not (root / "db" / "library.db").exists()


def test_ingest_dry_run_prints_header_without_creating_files(tmp_path: Path):
    library_root = tmp_path / "library"
    inbox = library_root / "inbox"
    inbox.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)
    before = _relative_files(library_root)

    runner = CliRunner()
    result = runner.invoke(
        main, ["ingest", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "path | hash16 | size (KB) | pages | validation | reason" in result.output
    assert _relative_files(library_root) - before == {
        Path("logs"),
        Path("logs/ingest.log"),
    }
    assert not (library_root / "records").exists()
    assert not (library_root / "text").exists()
    assert not (library_root / "db" / "library.db").exists()


def test_ingest_writes_non_empty_log_file(tmp_path: Path):
    library_root = tmp_path / "library"
    inbox = library_root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)

    result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )

    log_path = library_root / "logs" / "ingest.log"
    assert result.exit_code == 0
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert log_text
    assert "INFO paperlib.pipeline.ingest: ingest started" in log_text
    assert "INFO paperlib.pipeline.ingest: ingest finished" in log_text


def test_ingest_debug_flag_writes_debug_entries(tmp_path: Path):
    library_root = tmp_path / "library"
    (library_root / "inbox").mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)

    result = CliRunner().invoke(
        main, ["ingest", "--dry-run", "--debug", "--config", str(config_path)]
    )

    log_text = (library_root / "logs" / "ingest.log").read_text(
        encoding="utf-8"
    )
    assert result.exit_code == 0
    assert "DEBUG paperlib: debug logging enabled" in log_text


def test_ingest_no_ai_routes_to_ingest_library_without_ai(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(discovered=2, processed=2, summaries_skipped=2)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        ["ingest", "--no-ai", "--limit", "2", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert calls == [(2, False, True)]
    assert "summaries skipped:   2" in result.output
    assert "warnings:            0" in result.output


def test_cli_ingest_no_ai_on_synthetic_pdf_succeeds(tmp_path: Path):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "records written:     1" in result.output
    assert "summaries skipped:   1" in result.output
    assert len(list((root / "records").glob("p_*.json"))) == 1


def test_plain_ingest_routes_to_ingest_library_with_ai_mode(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(
            discovered=1,
            processed=1,
            records_written=1,
            summaries_generated=1,
        )

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main, ["ingest", "--limit", "1", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert calls == [(1, False, False)]
    assert "records written:     1" in result.output
    assert "summaries generated: 1" in result.output


def test_ingest_report_prints_all_fields_and_warnings(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        return IngestReport(
            discovered=3,
            processed=2,
            skipped_existing=1,
            failed=0,
            records_written=2,
            summaries_generated=1,
            summaries_failed=1,
            summaries_skipped=0,
            warnings=["AIError: unavailable"],
        )

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(main, ["ingest", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "discovered:          3" in result.output
    assert "processed:           2" in result.output
    assert "skipped existing:    1" in result.output
    assert "failed:              0" in result.output
    assert "records written:     2" in result.output
    assert "summaries generated: 1" in result.output
    assert "summaries failed:    1" in result.output
    assert "summaries skipped:   0" in result.output
    assert "warnings:            1" in result.output
    assert "Warnings:" in result.output
    assert "- AIError: unavailable" in result.output


def test_status_missing_db_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["status", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert (
        "No database found. Run paperlib ingest or paperlib rebuild-index."
        in result.output
    )


def test_status_existing_db_prints_counts(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        now = "2026-04-26T00:00:00Z"
        conn.executemany(
            """
            INSERT INTO papers (
                paper_id, metadata_status, summary_status,
                duplicate_status, review_status, record_path,
                created_at, updated_at
            )
            VALUES (?, 'ok', ?, 'unique', ?, ?, ?, ?)
            """,
            [
                ("p_one", "pending", "needs_review", "records/p_one.json", now, now),
                ("p_two", "failed", "reviewed", "records/p_two.json", now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO files (
                file_hash, paper_id, extraction_status, added_at
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                ("a" * 64, "p_one", "ok", now),
                ("b" * 64, "p_one", "partial", now),
                ("c" * 64, "p_two", "failed", now),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    result = CliRunner().invoke(
        main, ["status", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "papers:              2" in result.output
    assert "files:               3" in result.output
    assert "extraction ok:       1" in result.output
    assert "extraction partial:  1" in result.output
    assert "extraction failed:   1" in result.output
    assert "needs review:        1" in result.output
    assert "summary pending:     1" in result.output
    assert "summary failed:      1" in result.output


def test_status_after_cli_ingest_prints_counts(tmp_path: Path):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    ingest_result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )
    assert ingest_result.exit_code == 0

    result = CliRunner().invoke(
        main, ["status", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "papers:              1" in result.output
    assert "files:               1" in result.output


@pytest.mark.parametrize(
    (
        "lookup",
        "expect_handle",
        "expect_schema_version",
        "expect_handle_before_paper_id",
    ),
    [
        ("p_show", True, True, False),
        ("show_2024", True, False, True),
        ("arxiv:2401.12345", False, False, False),
        ("doi:10.1234/example", False, False, False),
        ("ABCDEF1234567890", False, False, False),
    ],
)
def test_show_by_identifier_prints_json(
    tmp_path: Path,
    lookup: str,
    expect_handle: bool,
    expect_schema_version: bool,
    expect_handle_before_paper_id: bool,
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", lookup, "--config", str(config_path)]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["paper_id"] == "p_show"
    if expect_handle:
        assert data["handle_id"] == "show_2024"
    if expect_schema_version:
        assert data["schema_version"] == 1
    if expect_handle_before_paper_id:
        assert result.output.index('"handle_id"') < result.output.index(
            '"paper_id"'
        )


def test_show_record_with_null_handle_id_does_not_crash(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    record = PaperRecord(paper_id="p_no_handle")
    write_record_atomic(records_dir / "p_no_handle.json", record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_no_handle.json")
    finally:
        conn.close()

    result = CliRunner().invoke(
        main, ["show", "p_no_handle", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["paper_id"] == "p_no_handle"
    assert data["handle_id"] is None


def test_show_nonexistent_id_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "p_missing", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "Paper not found: p_missing" in result.output
    assert "Supported namespaces" in result.output


@pytest.mark.parametrize(
    ("lookup", "expected_message"),
    [
        ("show_2024", "marked reviewed: show_2024"),
        ("p_show", None),
    ],
)
def test_mark_reviewed_by_identifier_updates_json_and_sqlite(
    tmp_path: Path,
    lookup: str,
    expected_message: str | None,
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["mark-reviewed", lookup, "--config", str(config_path)]
    )

    assert result.exit_code == 0
    if expected_message is not None:
        assert expected_message in result.output
    record = read_record(root / "records" / "p_show.json")
    assert record.status["review"] == "reviewed"
    assert record.review["locked"] is True
    assert record.review["reviewed_at"]
    assert record.timestamps["updated_at"] == record.review["reviewed_at"]
    conn = db.connect(root / "db" / "library.db")
    try:
        row = conn.execute(
            "SELECT review_status FROM papers WHERE paper_id = 'p_show'"
        ).fetchone()
    finally:
        conn.close()
    assert row["review_status"] == "reviewed"


def test_mark_reviewed_missing_id_fails_without_modifying_json(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)
    record_path = root / "records" / "p_show.json"
    before_json = record_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main, ["mark-reviewed", "missing_handle", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "Paper not found: missing_handle" in result.output
    assert record_path.read_text(encoding="utf-8") == before_json


def test_mark_reviewed_fills_old_review_object(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    record = PaperRecord(paper_id="p_old", handle_id="old_2024").to_dict()
    record["review"] = {"notes": "old shape"}
    write_record_atomic(records_dir / "p_old.json", record)
    loaded = read_record(records_dir / "p_old.json")

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, loaded, "records/p_old.json")
    finally:
        conn.close()

    result = CliRunner().invoke(
        main, ["mark-reviewed", "old_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    updated = read_record(records_dir / "p_old.json")
    assert updated.review["notes"] == "old shape"
    assert updated.review["locked"] is True
    assert updated.review["reviewed_at"]
    assert updated.status["review"] == "reviewed"


def test_delete_by_handle_id_removes_json_pdf_text_and_db_rows(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    fixture = _write_delete_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["delete", "delete_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "deleted: delete_2024" in result.output
    assert not fixture["record_path"].exists()
    assert not fixture["pdf_path"].exists()
    assert not fixture["text_path"].exists()
    assert fixture["deleted_pdf_path"].read_bytes() == b"%PDF delete fixture"
    conn = db.connect(root / "db" / "library.db")
    try:
        for table in ("processing_runs", "aliases", "files", "papers"):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_delete_by_paper_id_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    fixture = _write_delete_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["delete", "p_delete", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "deleted: delete_2024" in result.output
    assert not fixture["record_path"].exists()


@pytest.mark.parametrize("alias", ["doi:10.1234/delete", "arxiv:2401.99999"])
def test_delete_by_alias_works(tmp_path: Path, alias: str):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    fixture = _write_delete_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["delete", alias, "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "deleted: delete_2024" in result.output
    assert not fixture["record_path"].exists()


def test_delete_missing_canonical_pdf_warns_and_completes(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    fixture = _write_delete_fixture(root, config_path)
    fixture["pdf_path"].unlink()

    result = CliRunner().invoke(
        main, ["delete", "delete_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "Warning: canonical PDF missing" in result.output
    assert "deleted: delete_2024" in result.output
    assert not fixture["record_path"].exists()
    assert not fixture["text_path"].exists()


def test_delete_missing_text_file_does_not_raise(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    fixture = _write_delete_fixture(root, config_path)
    fixture["text_path"].unlink()

    result = CliRunner().invoke(
        main, ["delete", "delete_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "deleted: delete_2024" in result.output
    assert not fixture["record_path"].exists()
    assert fixture["deleted_pdf_path"].exists()


def test_delete_nonexistent_id_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_delete_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["delete", "missing_handle", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "Paper not found" in result.output


def test_show_after_delete_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_delete_fixture(root, config_path)

    delete_result = CliRunner().invoke(
        main, ["delete", "delete_2024", "--config", str(config_path)]
    )
    show_result = CliRunner().invoke(
        main, ["show", "delete_2024", "--config", str(config_path)]
    )

    assert delete_result.exit_code == 0
    assert show_result.exit_code != 0
    assert "Paper not found" in show_result.output


def test_list_after_delete_no_longer_shows_record(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_delete_fixture(root, config_path)

    delete_result = CliRunner().invoke(
        main, ["delete", "delete_2024", "--config", str(config_path)]
    )
    list_result = CliRunner().invoke(
        main, ["list", "--config", str(config_path)]
    )

    assert delete_result.exit_code == 0
    assert list_result.exit_code == 0
    assert "p_delete" not in list_result.output
    assert "delete_2024" not in list_result.output


def test_cli_show_by_paper_id_and_hash_after_ingest_print_valid_json(
    tmp_path: Path,
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    ingest_result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )
    assert ingest_result.exit_code == 0
    record_path = next((root / "records").glob("p_*.json"))
    record = read_record(record_path)
    hash_alias = next(
        alias for alias in record.identity.aliases if alias.startswith("hash:")
    )

    by_id = CliRunner().invoke(
        main, ["show", record.paper_id, "--config", str(config_path)]
    )
    by_hash = CliRunner().invoke(
        main, ["show", hash_alias, "--config", str(config_path)]
    )

    assert by_id.exit_code == 0
    assert by_hash.exit_code == 0
    assert json.loads(by_id.output)["paper_id"] == record.paper_id
    assert json.loads(by_hash.output)["paper_id"] == record.paper_id


def test_list_missing_db_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code != 0
    assert (
        "No database found. Run paperlib ingest or paperlib rebuild-index."
        in result.output
    )


def test_list_prints_missing_title_unknown_author_and_truncated_title(
    tmp_path: Path,
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    long_title = "L" * 61
    _insert_list_row(
        root,
        paper_id="p_missing",
        title=None,
        authors_json=None,
        year=None,
    )
    _insert_list_row(
        root,
        paper_id="p_long",
        handle_id="long_2025",
        title=long_title,
        authors_json='["Alexandria Cassandra Long", "Grace Hopper"]',
        year=2025,
        review_status="reviewed",
    )

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert LIST_HEADER in result.output
    assert (
        _expected_list_row(
            handle_id="<none>",
            paper_id="p_missing",
            year="<unknown>",
            first_author="<unknown>",
            title="<no title>",
            review_status="needs_review",
        )
        in result.output
    )
    assert (
        _expected_list_row(
            handle_id="long_2025",
            paper_id="p_long",
            year="2025",
            first_author="Alexandria Cassandra",
            title=f"{'L' * 54}...",
            review_status="reviewed",
        )
        in result.output
    )


def test_list_no_handle_hides_handle_column(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_row",
        handle_id="row_2024",
        title="A Row",
        authors_json='["Ada"]',
        year=2024,
    )

    result = CliRunner().invoke(
        main, ["list", "--no-handle", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert LIST_HEADER_NO_HANDLE in result.output
    assert "row_2024" not in result.output
    assert (
        _expected_list_row(
            paper_id="p_row",
            year="2024",
            first_author="Ada",
            title="A Row",
            review_status="needs_review",
        )
        in result.output
    )


def test_list_needs_review_filters_rows(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_needs",
        handle_id="needs_2024",
        title="Needs Review",
        authors_json='["Ada"]',
        year=2024,
        review_status="needs_review",
    )
    _insert_list_row(
        root,
        paper_id="p_reviewed",
        handle_id="reviewed_2025",
        title="Reviewed",
        authors_json='["Grace"]',
        year=2025,
        review_status="reviewed",
    )

    result = CliRunner().invoke(
        main, ["list", "--needs-review", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert (
        _expected_list_row(
            handle_id="needs_2024",
            paper_id="p_needs",
            year="2024",
            first_author="Ada",
            title="Needs Review",
            review_status="needs_review",
        )
        in result.output
    )
    assert "p_reviewed" not in result.output


def test_list_invalid_authors_json_prints_unknown(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_bad_authors",
        handle_id="bad_authors_2024",
        title="Bad Authors",
        authors_json="{bad json",
        year=2024,
    )

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (
        _expected_list_row(
            handle_id="bad_authors_2024",
            paper_id="p_bad_authors",
            year="2024",
            first_author="<unknown>",
            title="Bad Authors",
            review_status="needs_review",
        )
        in result.output
    )


def test_list_sort_handle_sorts_by_handle_id(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_zeta",
        handle_id="zeta_2024",
        title="Zeta",
        authors_json='["Zed"]',
        year=2024,
    )
    _insert_list_row(
        root,
        paper_id="p_alpha",
        handle_id="alpha_2024",
        title="Alpha",
        authors_json='["Ada"]',
        year=2023,
    )

    result = CliRunner().invoke(
        main, ["list", "--sort", "handle", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert (
        _expected_list_row(
            handle_id="zeta_2024",
            paper_id="p_zeta",
            year="2024",
            first_author="Zed",
            title="Zeta",
            review_status="needs_review",
        )
        in result.output
    )
    assert (
        _expected_list_row(
            handle_id="alpha_2024",
            paper_id="p_alpha",
            year="2023",
            first_author="Ada",
            title="Alpha",
            review_status="needs_review",
        )
        in result.output
    )
    assert result.output.index("alpha_2024") < result.output.index("zeta_2024")


def test_list_after_cli_ingest_prints_ingested_row(tmp_path: Path):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    ingest_result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )
    assert ingest_result.exit_code == 0
    record_path = next((root / "records").glob("p_*.json"))
    record = read_record(record_path)

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert record.paper_id in result.output
    assert (
        _expected_list_row(
            handle_id=record.handle_id or "<none>",
            paper_id=record.paper_id,
            year="2024",
            first_author="<unknown>",
            title="<no title>",
            review_status="needs_review",
        )
        in result.output
    )


def test_rebuild_index_restores_db_after_deleting_db(tmp_path: Path):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    ingest_result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )
    assert ingest_result.exit_code == 0
    db_path = root / "db" / "library.db"
    db_path.unlink()

    rebuild_result = CliRunner().invoke(
        main, ["rebuild-index", "--config", str(config_path)]
    )
    status_result = CliRunner().invoke(
        main, ["status", "--config", str(config_path)]
    )

    assert rebuild_result.exit_code == 0
    assert "records loaded: 1" in rebuild_result.output
    assert status_result.exit_code == 0
    assert "papers:              1" in status_result.output
    assert "files:               1" in status_result.output


def test_rebuild_index_dry_run_reports_handle_backfill_without_writes(
    tmp_path: Path,
):
    root = tmp_path / "library"
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    record = PaperRecord(paper_id="p_0440c911081cc43b").to_dict()
    record.pop("handle_id")
    record["metadata"]["year"]["value"] = 2024
    write_record_atomic(records_dir / "p_0440c911081cc43b.json", record)
    before_json = (records_dir / "p_0440c911081cc43b.json").read_text(
        encoding="utf-8"
    )

    result = CliRunner().invoke(
        main, ["rebuild-index", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "1 records would receive handle_id" in result.output
    assert (
        (records_dir / "p_0440c911081cc43b.json").read_text(encoding="utf-8")
        == before_json
    )
    assert not (root / "db" / "library.db").exists()


# ---------------------------------------------------------------------------
# export --bibtex tests
# ---------------------------------------------------------------------------

def _setup_export_record(
    root: Path,
    config_path: Path,
    *,
    paper_id: str = "p_export",
    handle_id: str = "export_2024",
    doi: str | None = "10.1234/export",
    title: str = "Export Paper",
    authors: list | None = None,
    year: int = 2024,
) -> PaperRecord:
    """Write a record to disk + DB and return it."""
    if authors is None:
        authors = ["Smith, J."]

    _write_config(config_path, root)
    records_dir = root / "records"
    records_dir.mkdir(parents=True, exist_ok=True)

    record = PaperRecord(paper_id=paper_id, handle_id=handle_id)
    record.identity.doi = doi
    record.metadata["title"].value = title
    record.metadata["authors"].value = authors
    record.metadata["year"].value = year

    write_record_atomic(records_dir / f"{paper_id}.json", record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, f"records/{paper_id}.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
    finally:
        conn.close()

    return record


def test_export_bibtex_no_db_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["export", "--bibtex", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "No database found" in result.output


def test_export_bibtex_no_ids_prints_all_records_to_stdout(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(root, config_path, paper_id="p_exp1", handle_id="exp1_2024")
    _setup_export_record(root, config_path, paper_id="p_exp2", handle_id="exp2_2024")

    result = CliRunner().invoke(
        main, ["export", "--bibtex", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "exp1_2024" in result.output
    assert "exp2_2024" in result.output
    assert "@article{" in result.output
    assert "}" in result.output


def test_export_bibtex_output_file_writes_file_and_prints_count(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(root, config_path)

    out_file = tmp_path / "output.bib"
    result = CliRunner().invoke(
        main,
        ["export", "--bibtex", "--output", str(out_file), "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "exported 1 records" in result.output
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "export_2024" in content
    assert "@article{" in content


def test_export_bibtex_specific_id_prints_single_entry(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(
        root, config_path, paper_id="p_single", handle_id="single_2024"
    )
    _setup_export_record(
        root, config_path, paper_id="p_other", handle_id="other_2024"
    )

    result = CliRunner().invoke(
        main, ["export", "--bibtex", "single_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "single_2024" in result.output
    assert "other_2024" not in result.output
    assert "@article{" in result.output
    assert "}" in result.output


def test_export_bibtex_unknown_id_exits_nonzero_with_paper_not_found(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(root, config_path)

    result = CliRunner().invoke(
        main,
        ["export", "--bibtex", "nonexistent_handle", "--config", str(config_path)],
    )

    assert result.exit_code != 0
    assert "Paper not found" in result.output


def test_export_bibtex_missing_json_exits_nonzero_cleanly(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(root, config_path)
    (root / "records" / "p_export.json").unlink()

    result = CliRunner().invoke(
        main, ["export", "--bibtex", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "Could not read record" in result.output
    assert "Traceback" not in result.output


def test_export_bibtex_output_contains_cite_key_and_closing_brace(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _setup_export_record(root, config_path, handle_id="mykey_2024")

    result = CliRunner().invoke(
        main, ["export", "--bibtex", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "@article{mykey_2024," in result.output
    assert result.output.strip().endswith("}")


# ---------------------------------------------------------------------------
# search command tests
# ---------------------------------------------------------------------------

def _write_summary_record(
    root: Path,
    paper_id: str,
    one_sentence: str,
    *,
    handle_id: str | None = None,
) -> None:
    """Write a JSON record with summary content to root/records/."""
    records_dir = root / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    record = PaperRecord(paper_id=paper_id, handle_id=handle_id)
    record.metadata["title"].value = "Summary Test Paper"
    record.metadata["authors"].value = ["Tester, T."]
    record.metadata["year"].value = 2024
    record.summary["one_sentence"] = one_sentence
    write_record_atomic(records_dir / f"{paper_id}.json", record)


def test_search_no_db_exits_nonzero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    result = CliRunner().invoke(
        main, ["search", "anything", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "No database found" in result.output


def test_search_matches_by_title_substring(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_sc1",
        title="Superconducting Qubit Research",
        authors_json='["Smith, J."]',
        year=2024,
        handle_id="qubit_2024",
    )

    result = CliRunner().invoke(
        main, ["search", "Superconducting", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "qubit_2024" in result.output
    assert "Superconducting" in result.output


def test_search_matches_by_author_substring(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_fey",
        title="Lectures on Physics",
        authors_json='["Feynman, R.P."]',
        year=1965,
        handle_id="feynman_1965",
    )

    result = CliRunner().invoke(
        main, ["search", "Feynman", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "feynman_1965" in result.output


def test_search_no_matches_prints_no_results_and_exits_zero(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_irr",
        title="Irrelevant Paper",
        authors_json='["Doe, J."]',
        year=2024,
    )

    result = CliRunner().invoke(
        main, ["search", "zzznomatch999", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "no results" in result.output


def test_search_field_title_does_not_search_authors(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    # title has no match, but authors_json does
    _insert_list_row(
        root,
        paper_id="p_tonly",
        title="Generic Physics Paper",
        authors_json='["UniqueAuthorXYZ"]',
        year=2024,
    )

    result = CliRunner().invoke(
        main,
        ["search", "UniqueAuthorXYZ", "--field", "title", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "no results" in result.output


def test_search_field_authors_does_not_search_title(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    # title has match, authors does not
    _insert_list_row(
        root,
        paper_id="p_aonly",
        title="UniqueTitleKeyword Paper",
        authors_json='["Generic Author"]',
        year=2024,
    )

    result = CliRunner().invoke(
        main,
        ["search", "UniqueTitleKeyword", "--field", "authors", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "no results" in result.output


def test_search_field_summary_matches_summary_content(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    # Need a real DB so the "no DB" guard passes
    _insert_list_row(
        root, paper_id="p_dummy", title="Dummy", authors_json='["Dummy"]', year=2024
    )
    # Write JSON record with summary (NOT in SQLite via upsert)
    _write_summary_record(
        root, "p_spinqubit", "A unique spin qubit discovery", handle_id="spinqubit_2024"
    )

    result = CliRunner().invoke(
        main,
        ["search", "spin qubit", "--field", "summary", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "spinqubit_2024" in result.output


def test_search_field_all_combines_sqlite_and_summary(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    # SQLite record — title matches query
    _insert_list_row(
        root,
        paper_id="p_jj1",
        title="Josephson Junction Device",
        authors_json='["Nakamura, Y."]',
        year=2024,
        handle_id="josephson_2024",
    )
    # Another SQLite record — title does NOT match; only its summary matches
    _insert_list_row(
        root,
        paper_id="p_jj2",
        title="Unrelated Title",
        authors_json='["Other Author"]',
        year=2023,
        handle_id="jj2_2023",
    )
    _write_summary_record(
        root, "p_jj2", "Josephson Junction analysis in circuit QED", handle_id="jj2_2023"
    )

    result = CliRunner().invoke(
        main,
        ["search", "Josephson Junction", "--field", "all", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "josephson_2024" in result.output
    assert "jj2_2023" in result.output


def test_search_output_header_matches_list_header(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_hdr",
        title="Header Check Paper",
        authors_json='["H, A."]',
        year=2024,
        handle_id="hdr_2024",
    )

    result = CliRunner().invoke(
        main, ["search", "Header Check", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line == LIST_HEADER


def test_search_column_widths_match_list_format(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_col",
        title="Column Width Paper",
        authors_json='["Author, B."]',
        year=2024,
        handle_id="col_2024",
    )

    result = CliRunner().invoke(
        main, ["search", "Column Width", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert len(lines) >= 2
    # Data row uses the same fixed-width formatting as list
    assert "col_2024" in lines[1]
    assert "Column Width Paper" in lines[1]
