from pathlib import Path

from click.testing import CliRunner

from conftest import _write_config, _write_minimal_pdf
from paperlib.cli import main
from paperlib.config import load_config
from paperlib.models.file import FileRecord
from paperlib.models.record import PaperRecord
from paperlib.store import db
from paperlib.store.json_store import write_record_atomic
from paperlib.store.validate_library import validate_library


def _config(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    return load_config(config_path), config_path


def _record() -> PaperRecord:
    record = PaperRecord(paper_id="p_validate", handle_id="validate_2024")
    record.identity.aliases = ["hash:aaaaaaaaaaaaaaaa"]
    record.metadata["title"].value = "Validate Me"
    record.metadata["authors"].value = ["Ada"]
    record.metadata["year"].value = 2024
    record.files.append(
        FileRecord(
            file_hash="a" * 64,
            original_filename="validate.pdf",
            canonical_path="papers/2024/validate.pdf",
            text_path="text/validate.txt",
            size_bytes=123,
            added_at="2026-04-26T00:00:00Z",
        )
    )
    return record


def _write_indexed_record(config, *, write_json: bool = True) -> dict:
    record = _record()
    record_path = config.paths.records / "p_validate.json"
    pdf_path = config.library.root / record.files[0].canonical_path
    text_path = config.library.root / record.files[0].text_path
    record_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF validate fixture")
    text_path.write_text("validate fixture text", encoding="utf-8")

    if write_json:
        write_record_atomic(record_path, record)

    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_validate.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
        db.insert_file(conn, record.paper_id, record.files[0])
    finally:
        conn.close()

    return {
        "record": record,
        "record_path": record_path,
        "pdf_path": pdf_path,
        "text_path": text_path,
    }


def _categories(findings):
    return [finding.category for finding in findings]


def test_clean_library_after_ingest_returns_empty_findings(tmp_path: Path):
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
    assert validate_library(load_config(config_path)) == []


def test_missing_db_returns_single_missing_db_error(tmp_path: Path):
    config, _config_path = _config(tmp_path)

    findings = validate_library(config)

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].category == "MISSING_DB"
    assert "SQLite database not found" in findings[0].detail


def test_bad_json_returns_bad_json_finding(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    config.paths.records.mkdir(parents=True)
    (config.paths.records / "bad.json").write_text("{bad json", encoding="utf-8")
    conn = db.connect(config.paths.db)
    db.init_db(conn)
    conn.close()

    findings = validate_library(config)

    assert _categories(findings) == ["BAD_JSON"]
    assert findings[0].severity == "error"


def test_json_record_absent_from_sqlite_returns_json_not_in_db(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config)
    conn = db.connect(config.paths.db)
    try:
        db.delete_paper(conn, fixture["record"].paper_id)
    finally:
        conn.close()

    findings = validate_library(config)

    assert _categories(findings) == ["JSON_NOT_IN_DB"]


def test_sqlite_record_path_missing_returns_db_not_in_json(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    record = _record()
    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_validate.json")
    finally:
        conn.close()

    findings = validate_library(config)

    assert _categories(findings) == ["DB_NOT_IN_JSON"]


def test_json_missing_canonical_path_returns_missing_pdf(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config)
    fixture["pdf_path"].unlink()

    findings = validate_library(config)

    assert _categories(findings) == ["MISSING_PDF"]


def test_json_missing_text_path_returns_missing_text(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config)
    fixture["text_path"].unlink()

    findings = validate_library(config)

    assert _categories(findings) == ["MISSING_TEXT"]


def test_json_paths_win_over_stale_sqlite_file_paths(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config)
    record = fixture["record"]
    record.files[0].canonical_path = "papers/2024/missing-from-json.pdf"
    record.files[0].text_path = "text/missing-from-json.txt"
    write_record_atomic(fixture["record_path"], record)

    findings = validate_library(config)

    categories = _categories(findings)
    assert "MISSING_PDF" in categories
    assert "MISSING_TEXT" in categories


def test_db_record_path_with_dot_slash_resolves_from_library_root(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    _write_indexed_record(config)
    conn = db.connect(config.paths.db)
    try:
        conn.execute(
            "UPDATE papers SET record_path = ? WHERE paper_id = ?",
            ("./records/p_validate.json", "p_validate"),
        )
        conn.commit()
    finally:
        conn.close()

    findings = validate_library(config)

    assert findings == []


def test_unreferenced_pdf_returns_orphan_pdf(tmp_path: Path):
    config, _config_path = _config(tmp_path)
    _write_indexed_record(config)
    orphan = config.paths.papers / "2024" / "orphan.pdf"
    orphan.write_bytes(b"%PDF orphan")

    findings = validate_library(config)

    assert _categories(findings) == ["ORPHAN_PDF"]
    assert findings[0].severity == "warning"


def test_cli_validate_library_exits_zero_and_prints_ok(tmp_path: Path):
    config, config_path = _config(tmp_path)
    _write_indexed_record(config)

    result = CliRunner().invoke(
        main, ["validate-library", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "ok — no issues found" in result.output


def test_cli_validate_library_exits_nonzero_on_error(tmp_path: Path):
    _config_obj, config_path = _config(tmp_path)

    result = CliRunner().invoke(
        main, ["validate-library", "--config", str(config_path)]
    )

    assert result.exit_code != 0
    assert "[ERROR] MISSING_DB:" in result.output
