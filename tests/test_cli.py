from pathlib import Path
import json

from click.testing import CliRunner

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
    record = PaperRecord(paper_id="p_show")
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
    assert data["schema_version"] == 1


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
    review_status: str = "needs_review",
) -> None:
    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        now = "2026-04-26T00:00:00Z"
        conn.execute(
            """
            INSERT INTO papers (
                paper_id, title, authors_json, year,
                metadata_status, summary_status, duplicate_status,
                review_status, record_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'ok', 'pending', 'unique', ?, ?, ?, ?)
            """,
            (
                paper_id,
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
        title=long_title,
        authors_json='["Ada Lovelace", "Grace Hopper"]',
        year=2025,
        review_status="reviewed",
    )

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "paper_id | year | first_author | title | review_status" in result.output
    assert "p_missing | <unknown> | <unknown> | <no title> | needs_review" in result.output
    assert f"p_long | 2025 | Ada Lovelace | {'L' * 57}... | reviewed" in result.output


def test_list_needs_review_filters_rows(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_needs",
        title="Needs Review",
        authors_json='["Ada"]',
        year=2024,
        review_status="needs_review",
    )
    _insert_list_row(
        root,
        paper_id="p_reviewed",
        title="Reviewed",
        authors_json='["Grace"]',
        year=2025,
        review_status="reviewed",
    )

    result = CliRunner().invoke(
        main, ["list", "--needs-review", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "p_needs | 2024 | Ada | Needs Review | needs_review" in result.output
    assert "p_reviewed" not in result.output


def test_list_invalid_authors_json_prints_unknown(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    _insert_list_row(
        root,
        paper_id="p_bad_authors",
        title="Bad Authors",
        authors_json="{bad json",
        year=2024,
    )

    result = CliRunner().invoke(main, ["list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "p_bad_authors | 2024 | <unknown> | Bad Authors | needs_review" in result.output


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
