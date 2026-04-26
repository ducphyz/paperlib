from pathlib import Path
import sqlite3

from paperlib.config import (
    AIConfig,
    AppConfig,
    ExtractionConfig,
    LibraryConfig,
    PathsConfig,
    PipelineConfig,
)
from paperlib.models import status
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.extract import ExtractionResult
from paperlib.pipeline.ingest import ingest_library
from paperlib.pipeline.validate import ValidationResult
from paperlib.store import db
from paperlib.store.json_store import read_record, write_record_atomic


def _config(root: Path) -> AppConfig:
    return AppConfig(
        library=LibraryConfig(root=root),
        paths=PathsConfig(
            inbox=root / "inbox",
            papers=root / "papers",
            records=root / "records",
            text=root / "text",
            db=root / "db" / "library.db",
            logs=root / "logs",
            failed=root / "failed",
            duplicates=root / "duplicates",
        ),
        pipeline=PipelineConfig(
            move_after_ingest=True,
            skip_existing=True,
            dry_run_default=False,
        ),
        extraction=ExtractionConfig(
            engine="pdfplumber",
            min_char_count=1,
            min_word_count=1,
        ),
        ai=AIConfig(
            enabled=False,
            provider="anthropic",
            model="test",
            max_tokens=100,
            temperature=0.0,
            anthropic_api_key=None,
        ),
    )


def _fake_extract(path: Path, *, min_char_count: int, min_word_count: int):
    raw_text = (
        "arXiv:2401.12345v2\n"
        "Published 12 March 2024\n"
        "DOI 10.1103/PhysRevLett.123.456\n"
        "body text"
    )
    return ExtractionResult(
        path=path,
        status=status.EXTRACTION_OK,
        engine="pdfplumber",
        engine_version="test",
        page_count=2,
        char_count=len(raw_text),
        word_count=len(raw_text.split()),
        quality=status.QUALITY_GOOD,
        warnings=[],
        raw_text=raw_text,
    )


def _create_runtime_dirs(config: AppConfig) -> None:
    for path in (
        config.paths.inbox,
        config.paths.papers,
        config.paths.records,
        config.paths.text,
        config.paths.db.parent,
        config.paths.logs,
        config.paths.failed,
        config.paths.duplicates,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _write_minimal_pdf(path: Path) -> None:
    text = (
        "arXiv:2401.12345v2 Published 12 March 2024 "
        "DOI 10.1103/PhysRevLett.123.456 paperlib test text"
    )
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


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _discovered(path: Path, file_hash: str) -> DiscoveredPDF:
    return DiscoveredPDF(
        path=path,
        file_hash=file_hash,
        hash16=file_hash[:16],
        hash8=file_hash[:8],
        size_bytes=path.stat().st_size,
        modified_time="2026-04-26T00:00:00Z",
    )


def test_ingest_library_dry_run_does_not_write_or_move(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    pdf_path = inbox / "paper.pdf"
    pdf_path.write_bytes(b"fake pdf")
    config = _config(root)

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(path, True, 1, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not extract")
        ),
    )

    report = ingest_library(config, dry_run=True)

    assert report.discovered == 1
    assert report.processed == 1
    assert pdf_path.exists()
    assert not config.paths.text.exists()
    assert not config.paths.records.exists()
    assert not config.paths.db.exists()


def test_ingest_library_writes_record_text_db_and_moves_pdf(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    pdf_path = inbox / "paper.pdf"
    pdf_path.write_bytes(b"fake pdf")
    config = _config(root)

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(path, True, 2, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        _fake_extract,
    )

    report = ingest_library(config, no_ai=True)

    assert report.discovered == 1
    assert report.processed == 1
    assert report.records_written == 1
    assert report.summaries_skipped == 1
    assert report.failed == 0
    assert not pdf_path.exists()

    record_paths = list(config.paths.records.glob("p_*.json"))
    assert len(record_paths) == 1
    record = read_record(record_paths[0])
    moved_pdf = root / record.files[0].canonical_path

    assert moved_pdf.exists()
    assert record.identity.doi == "10.1103/physrevlett.123.456"
    assert record.identity.arxiv_id == "2401.12345"
    assert record.metadata["year"].value == 2024
    assert record.metadata["title"].value is None
    assert record.summary["status"] == status.SUMMARY_SKIPPED
    text_path = config.paths.text / f"{record.files[0].file_hash[:16]}.txt"
    assert text_path.exists()
    assert config.paths.db.exists()


def test_ingest_library_skips_existing_file_hash_on_second_run(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    pdf_path = inbox / "paper.pdf"
    pdf_bytes = b"same fake pdf"
    pdf_path.write_bytes(pdf_bytes)
    config = _config(root)

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(path, True, 2, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        _fake_extract,
    )

    first = ingest_library(config, no_ai=True)
    assert first.records_written == 1

    pdf_path.write_bytes(pdf_bytes)
    second = ingest_library(config, no_ai=True)

    assert second.discovered == 1
    assert second.processed == 1
    assert second.skipped_existing == 1
    assert second.records_written == 0
    assert second.failed == 0
    assert pdf_path.exists()
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1


def test_ingest_library_reuses_record_for_existing_non_hash_alias(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    first_pdf = inbox / "first.pdf"
    first_pdf.write_bytes(b"first fake pdf")
    config = _config(root)

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(path, True, 2, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        _fake_extract,
    )

    first = ingest_library(config, no_ai=True)
    assert first.records_written == 1
    record_path = next(config.paths.records.glob("p_*.json"))
    original_record = read_record(record_path)
    original_record.summary["status"] = status.SUMMARY_GENERATED
    original_record.summary["one_sentence"] = "Existing summary."
    original_record.status["summary"] = status.SUMMARY_GENERATED
    write_record_atomic(record_path, original_record)

    second_pdf = inbox / "second.pdf"
    second_pdf.write_bytes(b"second fake pdf")
    second = ingest_library(config, no_ai=True)
    updated_record = read_record(record_path)

    assert second.records_written == 1
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert updated_record.paper_id == original_record.paper_id
    assert updated_record.status["duplicate"] == status.DUPLICATE_ALIAS
    assert updated_record.summary["status"] == status.SUMMARY_SKIPPED
    assert updated_record.summary["one_sentence"] == "Existing summary."
    assert updated_record.status["summary"] == status.SUMMARY_SKIPPED
    assert len(updated_record.files) == 2
    assert len(updated_record.identity.aliases) == len(
        set(updated_record.identity.aliases)
    )


def test_broken_pdf_moves_to_failed_logs_failure_and_batch_continues(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    config = _config(root)
    _create_runtime_dirs(config)
    broken_path = config.paths.inbox / "broken.pdf"
    valid_path = config.paths.inbox / "valid.pdf"
    broken_path.write_bytes(b"broken")
    valid_path.write_bytes(b"valid")
    broken = _discovered(broken_path, "b" * 64)
    valid = _discovered(valid_path, "a" * 64)

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.discover_pdfs",
        lambda inbox_path: [broken, valid],
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(
            path=path,
            ok=path == valid_path,
            page_count=1 if path == valid_path else None,
            has_text=path == valid_path,
            reason="ok" if path == valid_path else "broken pdf",
        ),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        _fake_extract,
    )

    report = ingest_library(config, no_ai=True, dry_run=False)

    assert report.discovered == 2
    assert report.processed == 2
    assert report.failed == 1
    assert report.records_written == 1
    assert not broken_path.exists()
    assert (config.paths.failed / "broken.pdf").exists()
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert len(list(config.paths.papers.rglob("*.pdf"))) == 1
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1
    assert _table_count(config.paths.db, "processing_runs") == 2

    with sqlite3.connect(config.paths.db) as conn:
        row = conn.execute(
            """
            SELECT stage, status, message
            FROM processing_runs
            WHERE file_hash = ?
            """,
            ("b" * 64,),
        ).fetchone()
    assert row[0] == "validate"
    assert row[1] == status.EXTRACTION_FAILED
    assert "broken pdf; moved to" in row[2]


def test_db_transaction_rolls_back_if_file_insert_fails(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    config = _config(root)
    _create_runtime_dirs(config)
    pdf_path = config.paths.inbox / "paper.pdf"
    pdf_path.write_bytes(b"fake pdf")

    monkeypatch.setattr(
        "paperlib.pipeline.ingest.validate_pdf",
        lambda path: ValidationResult(path, True, 2, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        _fake_extract,
    )

    original_insert_file_sql = db._insert_file_sql

    def fail_insert_file_sql(conn, paper_id, file_record):
        raise RuntimeError("insert_file failed")

    monkeypatch.setattr(db, "_insert_file_sql", fail_insert_file_sql)
    try:
        report = ingest_library(config, no_ai=True, dry_run=False)
    finally:
        monkeypatch.setattr(db, "_insert_file_sql", original_insert_file_sql)

    assert report.failed == 1
    assert report.records_written == 0
    assert len(report.warnings) == 1
    assert "insert_file failed" in report.warnings[0]
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert len(list(config.paths.text.glob("*.txt"))) == 1
    assert len(list(config.paths.papers.rglob("*.pdf"))) == 1
    assert _table_count(config.paths.db, "papers") == 0
    assert _table_count(config.paths.db, "aliases") == 0
    assert _table_count(config.paths.db, "files") == 0
    assert _table_count(config.paths.db, "processing_runs") == 0


def test_non_ai_ingest_is_idempotent_after_pdf_move(tmp_path: Path):
    root = tmp_path / "library"
    config = _config(root)
    _create_runtime_dirs(config)
    pdf_path = config.paths.inbox / "synthetic.pdf"
    _write_minimal_pdf(pdf_path)

    first = ingest_library(config, no_ai=True, dry_run=False)

    assert first.discovered >= 1
    assert first.processed == 1
    assert first.records_written == 1
    assert first.summaries_skipped == 1
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert len(list(config.paths.text.glob("*.txt"))) == 1
    assert len(list(config.paths.papers.rglob("*.pdf"))) == 1
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1

    second = ingest_library(config, no_ai=True, dry_run=False)

    assert second.discovered == 0
    assert second.processed == 0
    assert second.records_written == 0
    # Phase 5 moves the PDF out of inbox, so no second-run file is discovered.
    assert second.skipped_existing == 0
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1
    assert _table_count(config.paths.db, "aliases") == 3
    assert len(list(config.paths.records.glob("p_*.json"))) == 1
    assert len(list(config.paths.text.glob("*.txt"))) == 1
    assert len(list(config.paths.papers.rglob("*.pdf"))) == 1


def test_json_records_remain_source_of_truth_after_rebuild_index(
    tmp_path: Path,
):
    root = tmp_path / "library"
    config = _config(root)
    _create_runtime_dirs(config)
    pdf_path = config.paths.inbox / "synthetic.pdf"
    _write_minimal_pdf(pdf_path)

    first = ingest_library(config, no_ai=True, dry_run=False)

    assert first.records_written == 1
    record_path = next(config.paths.records.glob("p_*.json"))
    record = read_record(record_path)
    moved_pdf = root / record.files[0].canonical_path
    text_path = root / record.files[0].text_path

    assert moved_pdf.exists()
    assert text_path.exists()

    conn = db.connect(config.paths.db)
    try:
        before_counts = db.get_status_counts(conn)
    finally:
        conn.close()

    config.paths.db.unlink()

    result = db.rebuild_index_from_records(config.paths.db, config.paths.records)

    assert result["records_loaded"] == 1
    assert result["records_skipped"] == 0
    assert result["json_errors"] == 0

    conn = db.connect(config.paths.db)
    try:
        after_counts = db.get_status_counts(conn)
    finally:
        conn.close()

    assert after_counts == before_counts
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1
    assert _table_count(config.paths.db, "aliases") == 3
    assert _table_count(config.paths.db, "processing_runs") == 0
    assert moved_pdf.exists()
    assert text_path.exists()
