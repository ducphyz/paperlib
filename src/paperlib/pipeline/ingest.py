from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

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
from paperlib.pipeline.lookup import lookup_metadata
from paperlib.pipeline.summarise import (
    _mark_summary_skipped,
    locked_metadata,
    restore_locked_metadata,
    summarise_record,
)
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
from paperlib.utils import metadata_status, utc_now


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
    locked_skipped: int = 0
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
                existing_file_paper_id = db.find_paper_id_by_file_hash(
                    conn, pdf.file_hash
                )
                if existing_file_paper_id is not None:
                    existing_record = _load_existing_record(
                        config, conn, existing_file_paper_id
                    )
                    if _is_record_locked(existing_record):
                        report.locked_skipped += 1
                        logger.warning(
                            "%s: locked record skipped during ingest",
                            _record_label(existing_record),
                        )
                        continue
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
    conn: sqlite3.Connection,
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
    now = utc_now()
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
    if paper_id is None:
        paper_id = f"p_{pdf.hash16}"
        record = _new_record(
            paper_id=paper_id,
            aliases=aliases,
            metadata_values=metadata_values,
            metadata_fields=metadata_fields,
            now=now,
        )
        # handle_id assigned after lookup so enriched author/year are available
    else:
        record = _load_existing_record(config, conn, paper_id)
        if _is_record_locked(record):
            report.locked_skipped += 1
            logger.warning(
                "%s: locked record skipped during ingest",
                _record_label(record),
            )
            return

        locked_preserved = _merge_unlocked_metadata(
            record, metadata_fields, now
        )
        if locked_preserved:
            logger.warning(
                "%s: %s locked fields preserved",
                _record_label(record),
                locked_preserved,
            )
        if record.identity.doi is None and metadata_values["doi"] is not None:
            record.identity.doi = metadata_values["doi"]
        if (
            record.identity.arxiv_id is None
            and metadata_values["arxiv_id"] is not None
        ):
            record.identity.arxiv_id = metadata_values["arxiv_id"]
        record.status["duplicate"] = status_values.DUPLICATE_ALIAS
        record.identity.aliases = _merge_unique(record.identity.aliases, aliases)
        record.timestamps.setdefault("created_at", now)
        record.timestamps["updated_at"] = now

    if config.lookup.enabled:
        logger.info(
            "lookup starting: doi=%s arxiv_id=%s title_known=%s authors_known=%s",
            record.identity.doi,
            record.identity.arxiv_id,
            record.metadata["title"].value is not None,
            record.metadata["authors"].value is not None,
        )
        record, lookup_error = lookup_metadata(record, config.lookup, now)
        if lookup_error:
            logger.warning("lookup failed: %s", lookup_error)
        else:
            logger.info(
                "lookup ok: title=%s authors=%s",
                record.metadata["title"].value,
                record.metadata["authors"].value,
            )
    else:
        logger.debug("lookup disabled")

    # Assign handle_id for new records (after lookup may have filled author/year)
    if record.handle_id is None:
        record.handle_id = generate_handle_id(record, db.list_handle_ids(conn))

    file_record = _build_file_record(
        config, pdf, extraction, record.metadata, now
    )
    logger.info(
        "canonical filename decided: path=%s canonical_path=%s",
        pdf.path,
        file_record.canonical_path,
    )

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
        locked_fields_snapshot = locked_metadata(record)
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
        restored_count = restore_locked_metadata(record, locked_fields_snapshot)
        if restored_count:
            logger.warning(
                "%s: %s locked fields preserved after AI",
                _record_label(record),
                restored_count,
            )

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


def _load_existing_record(config: AppConfig, conn, paper_id: str) -> PaperRecord:
    record_path_value = db.get_record_path(conn, paper_id)
    if record_path_value is None:
        record_path = config.paths.records / f"{paper_id}.json"
    else:
        record_path = Path(record_path_value)
        if not record_path.is_absolute():
            record_path = config.library.root / record_path
    return read_record(record_path)


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    for value in incoming:
        if value not in merged:
            merged.append(value)
    return merged


def _merge_unlocked_metadata(
    record: PaperRecord, incoming_metadata: dict, now: str
) -> int:
    locked_preserved = 0
    for field_name, existing_field in record.metadata.items():
        if existing_field.locked:
            locked_preserved += 1
            continue

        incoming_field = incoming_metadata.get(field_name)
        if incoming_field is None or incoming_field.value is None:
            continue

        record.metadata[field_name] = incoming_field

    record.status["metadata"] = metadata_status(record)
    record.timestamps["updated_at"] = now
    return locked_preserved


def _record_label(record: PaperRecord) -> str:
    return record.handle_id or record.paper_id


def _is_record_locked(record: PaperRecord) -> bool:
    return bool(record.review.get("locked", False))
