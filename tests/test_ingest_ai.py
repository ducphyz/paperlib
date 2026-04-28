from __future__ import annotations

import sqlite3
from pathlib import Path

from paperlib.ai.client import AIError
from paperlib.config import (
    AIConfig,
    AppConfig,
    ExtractionConfig,
    LibraryConfig,
    PathsConfig,
    PipelineConfig,
)
from paperlib.models import status
from paperlib.pipeline.extract import ExtractionResult
from paperlib.pipeline.ingest import IngestReport, ingest_library
from paperlib.pipeline.validate import ValidationResult
from paperlib.store.json_store import read_record


def _config(root: Path, *, ai_enabled: bool = True) -> AppConfig:
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
            enabled=ai_enabled,
            provider="anthropic",
            model="claude-test",
            max_tokens=100,
            temperature=0.2,
            anthropic_api_key=None,
        ),
    )


def _write_pdf(path: Path, content: bytes = b"fake pdf") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


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
        "arXiv:2401.12345 Published 12 March 2024 "
        "DOI 10.1234/example paperlib AI failure test text"
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

    _write_pdf(path, bytes(content))


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _fake_validate(path: Path) -> ValidationResult:
    return ValidationResult(path, True, 1, True, "ok")


def _fake_extract(path: Path, *, min_char_count: int, min_word_count: int):
    raw_text = "arXiv:2401.12345\nDOI 10.1234/example\nbody text"
    return ExtractionResult(
        path=path,
        status=status.EXTRACTION_OK,
        engine="pdfplumber",
        engine_version="test",
        page_count=1,
        char_count=len(raw_text),
        word_count=len(raw_text.split()),
        quality=status.QUALITY_GOOD,
        warnings=[],
        raw_text=raw_text,
    )


def _only_record(config: AppConfig):
    record_path = next(config.paths.records.glob("p_*.json"))
    return read_record(record_path)


def test_no_ai_does_not_call_summarise_record_or_anthropic(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    pdf_path = root / "inbox" / "paper.pdf"
    _write_pdf(pdf_path)
    config = _config(root)

    monkeypatch.setattr("paperlib.pipeline.ingest.validate_pdf", _fake_validate)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf", _fake_extract
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.summarise_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no_ai must not call summarise_record")
        ),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.summarise.call_ai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no_ai must not call AI")
        ),
    )

    report = ingest_library(config, no_ai=True)
    record = _only_record(config)

    assert report.records_written == 1
    assert report.summaries_skipped == 1
    assert record.summary["status"] == status.SUMMARY_SKIPPED


def test_ingest_report_can_be_constructed_with_all_fields():
    report = IngestReport(
        discovered=1,
        processed=2,
        skipped_existing=3,
        failed=4,
        records_written=5,
        summaries_generated=6,
        summaries_failed=7,
        summaries_skipped=8,
        warnings=["warning"],
    )

    assert report.discovered == 1
    assert report.processed == 2
    assert report.skipped_existing == 3
    assert report.failed == 4
    assert report.records_written == 5
    assert report.summaries_generated == 6
    assert report.summaries_failed == 7
    assert report.summaries_skipped == 8
    assert report.warnings == ["warning"]


def test_config_ai_disabled_does_not_call_summarise_record(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    pdf_path = root / "inbox" / "paper.pdf"
    _write_pdf(pdf_path)
    config = _config(root, ai_enabled=False)

    monkeypatch.setattr("paperlib.pipeline.ingest.validate_pdf", _fake_validate)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf", _fake_extract
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.summarise_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled AI must not call summarise_record")
        ),
    )

    report = ingest_library(config, no_ai=False)
    record = _only_record(config)

    assert report.records_written == 1
    assert report.summaries_skipped == 1
    assert record.summary["status"] == status.SUMMARY_SKIPPED


def test_ai_success_writes_json_with_generated_summary(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    pdf_path = root / "inbox" / "paper.pdf"
    _write_pdf(pdf_path)
    config = _config(root)

    def fake_summarise_record(
        record, *, cleaned_text, source_file_hash, ai_config, no_ai
    ):
        record.summary["status"] = status.SUMMARY_GENERATED
        record.summary["source_file_hash"] = source_file_hash
        record.summary["one_sentence"] = "Generated summary."
        record.status["summary"] = status.SUMMARY_GENERATED
        return record, True, None

    monkeypatch.setattr("paperlib.pipeline.ingest.validate_pdf", _fake_validate)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf", _fake_extract
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.summarise_record", fake_summarise_record
    )

    report = ingest_library(config, no_ai=False)
    record = _only_record(config)

    assert report.records_written == 1
    assert report.summaries_generated == 1
    assert report.summaries_failed == 0
    assert report.failed == 0
    assert record.summary["status"] == status.SUMMARY_GENERATED
    assert record.status["summary"] == status.SUMMARY_GENERATED


def test_ai_failure_writes_json_with_failed_summary(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    pdf_path = root / "inbox" / "paper.pdf"
    _write_pdf(pdf_path)
    config = _config(root)

    def fake_summarise_record(
        record, *, cleaned_text, source_file_hash, ai_config, no_ai
    ):
        record.summary["status"] = status.SUMMARY_FAILED
        record.status["summary"] = status.SUMMARY_FAILED
        return record, False, "AIError: network unavailable"

    monkeypatch.setattr("paperlib.pipeline.ingest.validate_pdf", _fake_validate)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf", _fake_extract
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.summarise_record", fake_summarise_record
    )

    report = ingest_library(config, no_ai=False)
    record = _only_record(config)

    assert report.records_written == 1
    assert report.failed == 0
    assert report.summaries_failed == 1
    assert report.warnings == ["AIError: network unavailable"]
    assert record.summary["status"] == status.SUMMARY_FAILED
    assert record.status["summary"] == status.SUMMARY_FAILED


def test_exact_duplicate_does_not_call_ai(tmp_path: Path, monkeypatch):
    root = tmp_path / "library"
    pdf_path = root / "inbox" / "paper.pdf"
    pdf_bytes = b"same fake pdf"
    _write_pdf(pdf_path, pdf_bytes)
    config = _config(root)

    monkeypatch.setattr("paperlib.pipeline.ingest.validate_pdf", _fake_validate)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf", _fake_extract
    )

    first = ingest_library(config, no_ai=True)
    assert first.records_written == 1

    _write_pdf(pdf_path, pdf_bytes)
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.extract_text_from_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("exact duplicate must skip before extraction")
        ),
    )
    monkeypatch.setattr(
        "paperlib.pipeline.ingest.summarise_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("exact duplicate must not call AI")
        ),
    )

    second = ingest_library(config, no_ai=False)

    assert second.discovered == 1
    assert second.processed == 0
    assert second.skipped_existing == 1
    assert second.records_written == 0
    assert second.summaries_generated == 0
    assert second.summaries_failed == 0


def test_ai_failure_does_not_break_persistence_with_real_ingest_path(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    config = _config(root, ai_enabled=True)
    _create_runtime_dirs(config)
    pdf_path = config.paths.inbox / "synthetic.pdf"
    _write_minimal_pdf(pdf_path)

    def fail_call_ai(*args, **kwargs):
        raise AIError("deterministic AI failure")

    monkeypatch.setattr(
        "paperlib.pipeline.summarise.call_ai", fail_call_ai
    )

    report = ingest_library(config, no_ai=False, dry_run=False)

    record_paths = list(config.paths.records.glob("p_*.json"))
    assert report.failed == 0
    assert report.summaries_failed == 1
    assert report.records_written == 1
    assert len(record_paths) == 1
    assert report.warnings

    record = read_record(record_paths[0])
    moved_pdf = root / record.files[0].canonical_path
    text_path = root / record.files[0].text_path

    assert record.summary["status"] == status.SUMMARY_FAILED
    assert _table_count(config.paths.db, "papers") == 1
    assert _table_count(config.paths.db, "files") == 1
    assert moved_pdf.exists()
    assert config.paths.papers in moved_pdf.parents
    assert text_path.exists()
