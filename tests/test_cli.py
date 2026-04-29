from pathlib import Path
import json
import tomllib

from click.testing import CliRunner

from paperlib.__about__ import __version__
from paperlib.cli import main
from paperlib.models.record import PaperRecord
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.ingest import IngestReport
from paperlib.pipeline.validate import ValidationResult
from paperlib.store import db
from paperlib.store.json_store import read_record, write_record_atomic


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
"""
    )


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
    for command in (
        "validate-config",
        "ingest",
        "status",
        "list",
        "show",
        "rebuild-index",
        "mark-reviewed",
        "review",
    ):
        assert command in result.output


def test_each_command_help_exits_zero():
    for command in (
        "validate-config",
        "ingest",
        "status",
        "list",
        "show",
        "rebuild-index",
        "mark-reviewed",
        "review",
    ):
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


def _write_minimal_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    words = " ".join(f"word{i}" for i in range(140))
    text = (
        "arXiv:2401.12345 Published 12 March 2024 "
        f"DOI 10.1234/phase7 {words}"
    )

    try:
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError:
        _write_minimal_pdf_bytes(path, text)
        return

    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, text)
    pdf.save()


def _write_minimal_pdf_bytes(path: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        ),
    ]

    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(content)


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


def _write_show_fixture(root: Path, config_path: Path) -> None:
    _write_config(config_path, root)
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    record = PaperRecord(paper_id="p_show", handle_id="show_2024")
    record.identity.doi = "10.1234/example"
    record.identity.arxiv_id = "2401.12345"
    record.identity.aliases = [
        "hash:abcdef1234567890",
        "arxiv:2401.12345",
        "doi:10.1234/example",
    ]
    write_record_atomic(records_dir / "p_show.json", record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_show.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
    finally:
        conn.close()


def test_show_by_paper_id_prints_json(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "p_show", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["paper_id"] == "p_show"
    assert data["handle_id"] == "show_2024"
    assert data["schema_version"] == 1


def test_show_by_handle_id_works_and_prints_handle_near_top(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "show_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["paper_id"] == "p_show"
    assert data["handle_id"] == "show_2024"
    assert result.output.index('"handle_id"') < result.output.index('"paper_id"')


def test_show_by_arxiv_alias_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "arxiv:2401.12345", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["paper_id"] == "p_show"


def test_show_by_doi_alias_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "doi:10.1234/example", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["paper_id"] == "p_show"


def test_show_by_bare_hash_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["show", "ABCDEF1234567890", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["paper_id"] == "p_show"


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


def test_mark_reviewed_by_handle_updates_json_and_sqlite(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["mark-reviewed", "show_2024", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "marked reviewed: show_2024" in result.output
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


def test_mark_reviewed_by_paper_id_works(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_show_fixture(root, config_path)

    result = CliRunner().invoke(
        main, ["mark-reviewed", "p_show", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    record = read_record(root / "records" / "p_show.json")
    assert record.status["review"] == "reviewed"
    assert record.review["locked"] is True


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


def _insert_list_row(
    root: Path,
    *,
    paper_id: str,
    title,
    authors_json,
    year,
    handle_id: str | None = None,
    review_status: str = "needs_review",
) -> None:
    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        now = "2026-04-26T00:00:00Z"
        conn.execute(
            """
            INSERT INTO papers (
                paper_id, handle_id, title, authors_json, year,
                metadata_status, summary_status, duplicate_status,
                review_status, record_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'ok', 'pending', 'unique', ?, ?, ?, ?)
            """,
            (
                paper_id,
                handle_id,
                title,
                authors_json,
                year,
                review_status,
                f"records/{paper_id}.json",
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
        authors_json='["Ada Lovelace", "Grace Hopper"]',
        year=2025,
        review_status="reviewed",
    )

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert (
        "handle_id | paper_id | year | first_author | title | review_status"
        in result.output
    )
    assert (
        "<none> | p_missing | <unknown> | <unknown> | <no title> | needs_review"
        in result.output
    )
    assert (
        f"long_2025 | p_long | 2025 | Ada Lovelace | {'L' * 57}... | reviewed"
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
    assert "paper_id | year | first_author | title | review_status" in result.output
    assert "row_2024" not in result.output
    assert "p_row | 2024 | Ada | A Row | needs_review" in result.output


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
        "needs_2024 | p_needs | 2024 | Ada | Needs Review | needs_review"
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
        "bad_authors_2024 | p_bad_authors | 2024 | <unknown> | Bad Authors | "
        "needs_review"
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
    assert "<unknown> | <no title> | needs_review" in result.output


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
