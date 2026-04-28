from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from paperlib.config import AppConfig
from paperlib.handle import generate_handle_id
from paperlib.models import status as status_values
from paperlib.models.file import ExtractionInfo, FileRecord
from paperlib.models.identity import PaperIdentity, build_aliases
from paperlib.models.record import PaperRecord
from paperlib.pipeline.clean import clean_text
from paperlib.pipeline.discover import DiscoveredPDF, discover_pdfs
from paperlib.pipeline.extract import extract_text_from_pdf
from paperlib.pipeline.metadata import (
    build_non_ai_metadata_fields,
    extract_non_ai_metadata,
)
from paperlib.pipeline.summarise import summarise_record
from paperlib.pipeline.validate import validate_pdf
from paperlib.store import db
from paperlib.store.fs import (
    atomic_write_text,
    canonical_pdf_relative_path,
    ensure_runtime_dirs,
    move_file,
    move_to_failed,
)
from paperlib.store.json_store import read_record, write_record_atomic


logger = logging.getLogger("paperlib.pipeline.ingest")


@dataclass
class IngestReport:
    discovered: int = 0
    processed: int = 0
    skipped_existing: int = 0
    failed: int = 0
    records_written: int = 0
    summaries_generated: int = 0
    summaries_failed: int = 0
    summaries_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def ingest_library(
    config: AppConfig,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    no_ai: bool = False,
) -> IngestReport:
    logger.info(
        "ingest started: root=%s dry_run=%s no_ai=%s limit=%s",
        config.library.root,
        dry_run,
        no_ai,
        limit,
    )
    logger.debug(
        "ingest paths: inbox=%s records=%s text=%s db=%s",
        config.paths.inbox,
        config.paths.records,
        config.paths.text,
        config.paths.db,
    )
    if not config.library.root.exists():
        raise FileNotFoundError(
            f"Library root does not exist: {config.library.root}"
        )

    discovered = discover_pdfs(config.paths.inbox)
    selected = discovered[:limit] if limit is not None else discovered
    report = IngestReport(discovered=len(discovered))
    logger.info(
        "discovered PDFs: count=%s selected=%s",
        len(discovered),
        len(selected),
    )
    for pdf in selected:
        logger.info("discovered PDF: path=%s", pdf.path)
        logger.debug(
            "discovered PDF: path=%s hash16=%s size_bytes=%s",
            pdf.path,
            pdf.hash16,
            pdf.size_bytes,
        )
        logger.info("computed hash: path=%s hash16=%s", pdf.path, pdf.hash16)

    if dry_run:
        for pdf in selected:
            validation = validate_pdf(pdf.path)
            if validation.ok:
                logger.info("validation ok: path=%s", pdf.path)
            else:
                logger.warning(
                    "validation failed: path=%s reason=%s",
                    pdf.path,
                    validation.reason,
                )
            report.processed += 1
        logger.info(
            "ingest finished: discovered=%s processed=%s skipped_existing=%s "
            "failed=%s records_written=%s",
            report.discovered,
            report.processed,
            report.skipped_existing,
            report.failed,
            report.records_written,
        )
        return report

    ensure_runtime_dirs(config)
    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        for pdf in selected:
            try:
                if db.file_exists(conn, pdf.file_hash):
                    report.skipped_existing += 1
                    logger.info(
                        "exact duplicate skipped: path=%s hash16=%s",
                        pdf.path,
                        pdf.hash16,
                    )
                    continue
                report.processed += 1
                _ingest_pdf(config, conn, pdf, report, no_ai=no_ai)
            except Exception as exc:
                report.failed += 1
                report.warnings.append(f"{pdf.path}: {exc}")
                logger.exception("ingest failed: path=%s", pdf.path)
    finally:
        conn.close()

    logger.info(
        "ingest finished: discovered=%s processed=%s skipped_existing=%s "
        "failed=%s records_written=%s",
        report.discovered,
        report.processed,
        report.skipped_existing,
        report.failed,
        report.records_written,
    )
    return report


def _ingest_pdf(
    config: AppConfig,
    conn,
    pdf: DiscoveredPDF,
    report: IngestReport,
    *,
    no_ai: bool,
) -> None:
    validation = validate_pdf(pdf.path)
    if not validation.ok:
        failed_path = move_to_failed(pdf.path, config.paths.failed)
        logger.warning(
            "validation failed: path=%s reason=%s", pdf.path, validation.reason
        )
        logger.info("failed PDF moved to failed/: path=%s", failed_path)
        db.log_processing_run(
            conn,
            pdf.file_hash,
            None,
            "validate",
            status_values.EXTRACTION_FAILED,
            f"{validation.reason}; moved to {failed_path}",
        )
        report.failed += 1
        return
    logger.info("validation ok: path=%s", pdf.path)

    extraction = extract_text_from_pdf(
        pdf.path,
        min_char_count=config.extraction.min_char_count,
        min_word_count=config.extraction.min_word_count,
    )
    if extraction.status == status_values.EXTRACTION_FAILED:
        logger.warning(
            "extraction failed: path=%s warnings=%s",
            pdf.path,
            len(extraction.warnings),
        )
    else:
        logger.info(
            "extraction ok: path=%s status=%s pages=%s chars=%s words=%s",
            pdf.path,
            extraction.status,
            extraction.page_count,
            extraction.char_count,
            extraction.word_count,
        )
    cleaned_text = clean_text(extraction.raw_text)
    metadata_values = extract_non_ai_metadata(cleaned_text, pdf.path.name)
    now = _utc_now()
    metadata_fields = build_non_ai_metadata_fields(
        year=metadata_values["year"],
        year_confidence=metadata_values["year_confidence"],
        doi=metadata_values["doi"],
        arxiv_id=metadata_values["arxiv_id"],
        embedded_pdf_metadata=extraction.embedded_metadata,
        original_filename=pdf.path.name,
        now_iso=now,
    )
    logger.info(
        "metadata extraction completed: path=%s doi=%s arxiv_id=%s",
        pdf.path,
        "present" if metadata_values["doi"] else "absent",
        "present" if metadata_values["arxiv_id"] else "absent",
    )
    aliases = build_aliases(
        pdf.hash16,
        doi=metadata_values["doi"],
        arxiv_id=metadata_values["arxiv_id"],
    )
    paper_id = _find_existing_paper_id(conn, aliases[1:])
    file_record = _build_file_record(
        config, pdf, extraction, metadata_fields, now
    )
    logger.info(
        "canonical filename decided: path=%s canonical_path=%s",
        pdf.path,
        file_record.canonical_path,
    )

    if paper_id is None:
        paper_id = f"p_{pdf.hash16}"
        record = _new_record(
            paper_id=paper_id,
            aliases=aliases,
            metadata_values=metadata_values,
            metadata_fields=metadata_fields,
            now=now,
        )
        record.handle_id = generate_handle_id(record, db.list_handle_ids(conn))
    else:
        record = read_record(config.paths.records / f"{paper_id}.json")
        record.status["duplicate"] = status_values.DUPLICATE_ALIAS
        record.identity.aliases = _merge_unique(record.identity.aliases, aliases)
        record.timestamps.setdefault("created_at", now)
        record.timestamps["updated_at"] = now

    if not any(existing.file_hash == pdf.file_hash for existing in record.files):
        record.files.append(file_record)

    canonical_path = config.library.root / file_record.canonical_path
    move_file(pdf.path, canonical_path)
    logger.info("PDF moved: from=%s to=%s", pdf.path, canonical_path)
    atomic_write_text(config.paths.text / f"{pdf.hash16}.txt", cleaned_text)
    logger.info(
        "text written: path=%s", config.paths.text / f"{pdf.hash16}.txt"
    )

    if no_ai or not config.ai.enabled:
        _mark_summary_skipped(record)
    elif not record.summary.get("locked", False):
        record, generated, error_message = summarise_record(
            record,
            cleaned_text=cleaned_text,
            source_file_hash=pdf.file_hash,
            ai_config=config.ai,
            no_ai=no_ai,
        )
        if generated:
            report.summaries_generated += 1
        if (
            error_message is not None
            and record.status.get("summary") == status_values.SUMMARY_FAILED
        ):
            report.summaries_failed += 1
        if error_message is not None:
            report.warnings.append(error_message)

    if record.status.get("summary") == status_values.SUMMARY_SKIPPED:
        report.summaries_skipped += 1

    record_path = config.paths.records / f"{record.paper_id}.json"
    write_record_atomic(record_path, record)
    logger.info("JSON written: path=%s", record_path)

    db.record_ingest_success(
        conn,
        record,
        file_record,
        f"records/{record.paper_id}.json",
    )
    logger.info("database updated: paper_id=%s", record.paper_id)
    report.records_written += 1


def _build_file_record(
    config: AppConfig,
    pdf: DiscoveredPDF,
    extraction,
    metadata_fields: dict,
    now: str,
) -> FileRecord:
    authors = metadata_fields["authors"].value
    first_author = None
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, str) and first.strip():
            first_author = first.strip()

    canonical_path = canonical_pdf_relative_path(
        year=metadata_fields["year"].value,
        first_author=first_author,
        file_hash=pdf.file_hash,
    )
    return FileRecord(
        file_hash=pdf.file_hash,
        original_filename=pdf.path.name,
        canonical_path=canonical_path,
        text_path=f"text/{pdf.hash16}.txt",
        size_bytes=pdf.size_bytes,
        added_at=now,
        extraction=ExtractionInfo(
            status=extraction.status,
            engine=extraction.engine,
            engine_version=extraction.engine_version,
            page_count=extraction.page_count,
            char_count=extraction.char_count,
            word_count=extraction.word_count,
            quality=extraction.quality,
            warnings=list(extraction.warnings),
        ),
    )


def _new_record(
    *,
    paper_id: str,
    aliases: list[str],
    metadata_values: dict,
    metadata_fields: dict,
    now: str,
) -> PaperRecord:
    record = PaperRecord(
        paper_id=paper_id,
        identity=PaperIdentity(
            doi=metadata_values["doi"],
            arxiv_id=metadata_values["arxiv_id"],
            aliases=aliases,
        ),
        metadata=metadata_fields,
        timestamps={"created_at": now, "updated_at": now},
    )
    record.status["duplicate"] = status_values.DUPLICATE_UNIQUE
    if any(
        metadata_values[key] is not None for key in ("doi", "arxiv_id")
    ) or any(
        field_value.value is not None
        for field_value in metadata_fields.values()
    ):
        record.status["metadata"] = status_values.METADATA_PARTIAL
    record.summary["status"] = status_values.SUMMARY_SKIPPED
    record.summary["source_file_hash"] = None
    record.status["summary"] = status_values.SUMMARY_SKIPPED
    return record


def _find_existing_paper_id(conn, aliases: list[str]) -> str | None:
    for alias in aliases:
        paper_id = db.find_paper_id_by_alias(conn, alias)
        if paper_id is not None:
            return paper_id
    return None


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    for value in incoming:
        if value not in merged:
            merged.append(value)
    return merged


def _mark_summary_skipped(record: PaperRecord) -> None:
    if record.summary.get("locked", False):
        return
    record.summary["status"] = status_values.SUMMARY_SKIPPED
    record.status["summary"] = status_values.SUMMARY_SKIPPED


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
