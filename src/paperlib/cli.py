from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import click

from paperlib.__about__ import __description__, __title__, __version__
from paperlib.config import AppConfig, load_config
from paperlib.logging_config import setup_logging
from paperlib.models.record import PaperRecord
from paperlib.pipeline.discover import discover_pdfs
from paperlib.pipeline.ingest import ingest_library
from paperlib.pipeline.summarise import summarise_record, locked_metadata, restore_locked_metadata
from paperlib.pipeline.validate import validate_pdf
from paperlib.review import ReviewCancelled, review_record_interactive
from paperlib.store import db
from paperlib.store.fs import move_to_deleted
from paperlib.store.json_store import (
    read_record,
    write_record_atomic,
)
from paperlib.store.validate_library import validate_library
from paperlib.utils import resolve_library_path, utc_now


CONFIG_HELP = "Path to the PaperLib config TOML file."
_LIST_SEPARATOR = "  "
_LIST_COLUMN_WIDTHS = {
    "handle_id": 20,
    "paper_id": 18,
    "year": 9,
    "first_author": 20,
    "title": 57,
    "review_status": 12,
}
_LIST_COLUMNS = ("paper_id", "year", "first_author", "title", "review_status")


@click.group(name=__title__, help=__description__)
@click.option(
    "--config",
    "global_config_path",
    default=None,
    help=CONFIG_HELP,
)
@click.version_option(__version__, prog_name=__title__)
@click.pass_context
def main(ctx: click.Context, global_config_path: str | None) -> None:
    if global_config_path is None:
        return

    default_map = dict(ctx.default_map or {})
    for command_name in main.commands:
        command_defaults = dict(default_map.get(command_name, {}))
        command_defaults.setdefault("config_path", global_config_path)
        default_map[command_name] = command_defaults
    ctx.default_map = default_map


@main.command("validate-config", help="Validate config and runtime paths.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def validate_config(config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

    path_statuses = _ensure_runtime_paths(config)

    click.echo(f"Config: {config_path}")
    click.echo(f"Library root: {config.library.root}")
    click.echo()
    click.echo("Path status:")
    for name, path, state in path_statuses:
        click.echo(f"{name:<11} {str(path):<44} {state}")

    click.echo()
    click.echo("AI:")
    click.echo(f"enabled: {str(config.ai.enabled).lower()}")
    if config.ai.enabled and not config.ai.anthropic_api_key:
        click.echo(
            f"{config.ai.api_key_env}: missing, non-AI commands still work"
        )
    elif config.ai.enabled:
        click.echo(f"{config.ai.api_key_env}: present")
    else:
        click.echo(f"{config.ai.api_key_env}: not required")


@main.command("validate-library", help="Validate indexed library files.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def validate_library_command(config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    findings = validate_library(config)
    if not findings:
        click.echo("ok — no issues found")
        return

    for finding in findings:
        click.echo(
            f"[{finding.severity.upper()}] "
            f"{finding.category}: {finding.detail}"
        )

    if any(finding.severity == "error" for finding in findings):
        raise click.exceptions.Exit(1)


@main.command("re-summarise", help="Re-generate AI summaries for records that failed or were skipped.")
@click.argument("id_or_alias", nargs=1, required=False)
@click.option("--limit", type=int, help="Process at most this many eligible records.")
@click.option("--no-ai", is_flag=True, help="Skip AI summarization for this run.")
@click.option("--debug", is_flag=True, help="Write DEBUG-level log entries.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def resummarise_command(id_or_alias: str | None, limit: int | None, no_ai: bool, debug: bool, config_path: str) -> None:
    from paperlib.pipeline.clean import clean_text
    from paperlib.store.db import list_resummary_candidates
    
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)
    logger = _setup_command_logging(config, debug=debug)
    logger.info("config loaded: %s", config_path)
    logger.info("resummarise started: id_or_alias=%s limit=%s no_ai=%s", id_or_alias, limit, no_ai)

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    # Initialize counters
    eligible_count = 0
    processed_count = 0
    generated_count = 0
    failed_count = 0
    skipped_locked_count = 0
    skipped_no_text_count = 0

    conn = db.connect(db_path)
    try:
        if id_or_alias:
            # Process specific record - ignore summary_status and re-summarise regardless
            record = _load_record_by_id(conn, config, id_or_alias)
            paper_id = record.paper_id
            record_path_value = db.get_record_path(conn, paper_id)
            if record_path_value is None:
                raise click.ClickException(f"Record path not found for: {paper_id}")

            # Process this specific record directly
            candidate = {'paper_id': paper_id, 'record_path': record_path_value, 'handle_id': id_or_alias}
            candidates = [candidate]
            # For specific ID, we don't count as eligible since we process regardless of status
            
            paper_id = candidate['paper_id']
            record_path_str = candidate['record_path']
            record_path = resolve_library_path(config.library.root, record_path_str)

            try:
                # Skip if record.summary["locked"] is True
                if record.summary.get("locked", False):
                    skipped_locked_count += 1
                    logger.info("skipping locked record: paper_id=%s", paper_id)
                else:
                    # Select source file: prefer the FileRecord whose file_hash matches record.summary["source_file_hash"]
                    source_file_hash = record.summary.get("source_file_hash")
                    source_file_record = None
                    
                    if source_file_hash:
                        for file_record in record.files:
                            if file_record.file_hash == source_file_hash:
                                source_file_record = file_record
                                break
                    
                    # Fall back to record.files[0] if no match found
                    if not source_file_record and record.files:
                        source_file_record = record.files[0]

                    # Check if source file record exists and text file is accessible
                    if not source_file_record or not source_file_record.text_path:
                        skipped_no_text_count += 1
                        logger.warning("skipping record with no source file: paper_id=%s", paper_id)
                    else:
                        # Resolve text path
                        text_path = Path(source_file_record.text_path)
                        if not text_path.is_absolute():
                            text_path = config.library.root / text_path

                        if not text_path.exists():
                            skipped_no_text_count += 1
                            logger.warning("skipping record, text file missing: paper_id=%s text_path=%s", paper_id, text_path)
                        else:
                            # Read text file content and clean it
                            text_content = text_path.read_text(encoding="utf-8")
                            cleaned_text = clean_text(text_content)

                            # Prepare for summarization
                            locked_fields = locked_metadata(record)
                            
                            # Perform summarisation
                            updated_record, generated, error_msg = summarise_record(
                                record,
                                cleaned_text=cleaned_text,
                                source_file_hash=source_file_record.file_hash,
                                ai_config=config.ai,
                                no_ai=no_ai
                            )
                            
                            # Restore locked metadata
                            restore_locked_metadata(updated_record, locked_fields)

                            # Write updated record back to disk
                            write_record_atomic(record_path, updated_record)

                            # Update database index
                            db.update_record_index(conn, updated_record, record_path_str)

                            processed_count += 1
                            if generated:
                                generated_count += 1
                            if error_msg:
                                failed_count += 1
                                logger.warning("summarization failed for paper_id=%s: %s", paper_id, error_msg)

                            logger.info("processed record: paper_id=%s generated=%s", paper_id, generated)

            except click.ClickException:
                raise
            except Exception as e:
                failed_count += 1
                logger.error("error processing record: paper_id=%s error=%s", paper_id, str(e))
        else:
            # Process eligible records (status failed or skipped)
            candidates = list_resummary_candidates(conn, limit=limit)
            eligible_count = len(candidates)

            for candidate in candidates:
                paper_id = candidate['paper_id']
                record_path_str = candidate['record_path']
                record_path = resolve_library_path(
                    config.library.root,
                    record_path_str,
                )

                try:
                    record = read_record(record_path)
                    
                    # Skip if record.summary["locked"] is True
                    if record.summary.get("locked", False):
                        skipped_locked_count += 1
                        logger.info("skipping locked record: paper_id=%s", paper_id)
                        continue

                    # Select source file: prefer the FileRecord whose file_hash matches record.summary["source_file_hash"]
                    source_file_hash = record.summary.get("source_file_hash")
                    source_file_record = None
                    
                    if source_file_hash:
                        for file_record in record.files:
                            if file_record.file_hash == source_file_hash:
                                source_file_record = file_record
                                break
                    
                    # Fall back to record.files[0] if no match found
                    if not source_file_record and record.files:
                        source_file_record = record.files[0]

                    # Check if source file record exists and text file is accessible
                    if not source_file_record or not source_file_record.text_path:
                        skipped_no_text_count += 1
                        logger.warning("skipping record with no source file: paper_id=%s", paper_id)
                        continue

                    # Resolve text path
                    text_path = Path(source_file_record.text_path)
                    if not text_path.is_absolute():
                        text_path = config.library.root / text_path

                    if not text_path.exists():
                        skipped_no_text_count += 1
                        logger.warning("skipping record, text file missing: paper_id=%s text_path=%s", paper_id, text_path)
                        continue

                    # Read text file content and clean it
                    text_content = text_path.read_text(encoding="utf-8")
                    cleaned_text = clean_text(text_content)

                    # Prepare for summarization
                    locked_fields = locked_metadata(record)
                    
                    # Perform summarisation
                    updated_record, generated, error_msg = summarise_record(
                        record,
                        cleaned_text=cleaned_text,
                        source_file_hash=source_file_record.file_hash,
                        ai_config=config.ai,
                        no_ai=no_ai
                    )
                    
                    # Restore locked metadata
                    restore_locked_metadata(updated_record, locked_fields)

                    # Write updated record back to disk
                    write_record_atomic(record_path, updated_record)

                    # Update database index
                    db.update_record_index(conn, updated_record, record_path_str)

                    processed_count += 1
                    if generated:
                        generated_count += 1
                    if error_msg:
                        failed_count += 1
                        logger.warning("summarization failed for paper_id=%s: %s", paper_id, error_msg)

                    logger.info("processed record: paper_id=%s generated=%s", paper_id, generated)

                except Exception as e:
                    failed_count += 1
                    logger.error("error processing record: paper_id=%s error=%s", paper_id, str(e))

    finally:
        conn.close()

    # Print summary report
    if id_or_alias:
        # When processing specific ID, eligible count should be 0 since we're bypassing eligibility check
        click.echo(f"{'eligible:':<21} 0")
    else:
        click.echo(f"{'eligible:':<21} {eligible_count}")
    click.echo(f"{'processed:':<21} {processed_count}")
    click.echo(f"{'generated:':<21} {generated_count}")
    click.echo(f"{'failed:':<21} {failed_count}")
    click.echo(f"{'skipped locked:':<21} {skipped_locked_count}")
    click.echo(f"{'skipped no text:':<21} {skipped_no_text_count}")

    logger.info(
        "resummarise finished: eligible=%s processed=%s generated=%s failed=%s "
        "skipped_locked=%s skipped_no_text=%s",
        eligible_count if not id_or_alias else 0, processed_count, generated_count,
        failed_count, skipped_locked_count, skipped_no_text_count
    )


@main.command("ingest", help="Ingest PDFs from the configured inbox.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Discover and validate PDFs without writing files, DB rows, or AI calls.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most this many discovered PDFs.",
)
@click.option("--no-ai", is_flag=True, help="Skip AI summarization for this run.")
@click.option("--debug", is_flag=True, help="Write DEBUG-level log entries.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def ingest(
    dry_run: bool,
    limit: int | None,
    no_ai: bool,
    debug: bool,
    config_path: str,
) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)
    logger = _setup_command_logging(config, debug=debug)
    logger.info("config loaded: %s", config_path)

    if dry_run:
        discovered = discover_pdfs(config.paths.inbox)
        to_process = discovered[:limit] if limit is not None else discovered
        _print_dry_run_table(to_process)
        report = ingest_library(
            config,
            limit=limit,
            dry_run=True,
            no_ai=True,
        )
        click.echo()
        _print_ingest_report(report)
        return

    report = ingest_library(
        config,
        limit=limit,
        dry_run=False,
        no_ai=no_ai,
    )
    _print_ingest_report(report)


@main.command("rebuild-index", help="Rebuild SQLite from canonical JSON records.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report index and handle backfill changes without writing.",
)
@click.option(
    "--no-backfill",
    is_flag=True,
    help="Do not write missing handle_id values back to JSON records.",
)
@click.option("--debug", is_flag=True, help="Write DEBUG-level log entries.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def rebuild_index(
    dry_run: bool,
    no_backfill: bool,
    debug: bool,
    config_path: str,
) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)
    logger = _setup_command_logging(config, debug=debug)
    logger.info("config loaded: %s", config_path)
    logger.info(
        "rebuild-index started: dry_run=%s backfill_handles=%s",
        dry_run,
        not no_backfill,
    )
    result = db.rebuild_index_from_records(
        config.paths.db,
        config.paths.records,
        dry_run=dry_run,
        backfill_handles=not no_backfill,
    )
    logger.info(
        "rebuild-index finished: records_loaded=%s records_skipped=%s "
        "json_errors=%s handles_added=%s duplicate_handles_repaired=%s",
        result["records_loaded"],
        result["records_skipped"],
        result["json_errors"],
        result["handles_added"],
        result["duplicate_handles_repaired"],
    )

    click.echo(f"records loaded: {result['records_loaded']}")
    click.echo(f"records skipped: {result['records_skipped']}")
    click.echo(f"JSON errors encountered: {result['json_errors']}")
    if dry_run:
        click.echo(f"{result['handles_added']} records would receive handle_id")
        click.echo(
            f"{result['duplicate_handles_repaired']} duplicate handle_id "
            "would be repaired"
        )
    else:
        click.echo(f"handle IDs added: {result['handles_added']}")
        click.echo(
            "duplicate handle IDs repaired: "
            f"{result['duplicate_handles_repaired']}"
        )
    if result["backup_path"] is not None:
        click.echo(f"backup: {result['backup_path']}")


@main.command("status", help="Show library and processing status counts.")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def status(config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    db_path = config.paths.db
    if not db_path.exists():
        click.echo(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )
        raise click.exceptions.Exit(1)

    conn = db.connect(db_path)
    try:
        counts = db.get_status_counts(conn)
    finally:
        conn.close()

    click.echo(f"{'papers:':<21}{counts['papers']}")
    click.echo(f"{'files:':<21}{counts['files']}")
    click.echo(f"{'extraction ok:':<21}{counts['extraction_ok']}")
    click.echo(
        f"{'extraction partial:':<21}{counts['extraction_partial']}"
    )
    click.echo(f"{'extraction failed:':<21}{counts['extraction_failed']}")
    click.echo(f"{'needs review:':<21}{counts['needs_review']}")
    click.echo(f"{'summary pending:':<21}{counts['summary_pending']}")
    click.echo(f"{'summary failed:':<21}{counts['summary_failed']}")


def _load_record_by_id(
    conn: sqlite3.Connection,
    config: AppConfig,
    id_or_alias: str,
) -> PaperRecord:
    try:
        paper_id = db.resolve_id(conn, id_or_alias)
    except db.IdNotFound as exc:
        raise click.ClickException(str(exc)) from exc
    if paper_id is None:
        raise click.ClickException(f"Record not found: {id_or_alias}")
    record_path_value = db.get_record_path(conn, paper_id)
    if record_path_value is None:
        raise click.ClickException(f"Record path not found for: {paper_id}")
    record_path = resolve_library_path(config.library.root, record_path_value)
    try:
        return read_record(record_path)
    except Exception as exc:
        raise click.ClickException(
            f"Could not read record {record_path}: {exc}"
        ) from exc


@main.command("show", help="Show one paper record as JSON.")
@click.argument("id_or_alias")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def show(id_or_alias: str, config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    try:
        record = _load_record_by_id(conn, config, id_or_alias)
    finally:
        conn.close()

    click.echo(
        json.dumps(
            _format_show_record(record.to_dict()),
            indent=2,
            ensure_ascii=False,
        )
    )


@main.command("delete", help="Delete a paper record and move PDFs aside.")
@click.argument("id_or_alias")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def delete(id_or_alias: str, config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    try:
        try:
            paper_id = db.resolve_id(conn, id_or_alias)
        except db.IdNotFound as exc:
            raise click.ClickException(str(exc)) from exc
        record_path_value = db.get_record_path(conn, paper_id)
    finally:
        conn.close()

    if record_path_value is None:
        raise click.ClickException(f"Record path not found for: {paper_id}")

    record_path = resolve_library_path(config.library.root, record_path_value)

    try:
        record = read_record(record_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    conn = db.connect(db_path)
    try:
        db.delete_paper(conn, paper_id)
    finally:
        conn.close()

    for file_record in record.files:
        pdf_path = resolve_library_path(
            config.library.root,
            file_record.canonical_path,
        )
        if pdf_path.exists():
            move_to_deleted(pdf_path, config.paths.deleted)
        else:
            click.echo(f"Warning: canonical PDF missing: {pdf_path}")

    for file_record in record.files:
        text_path = resolve_library_path(
            config.library.root,
            file_record.text_path,
        )
        text_path.unlink(missing_ok=True)

    record_path.unlink()
    click.echo(f"deleted: {record.handle_id or record.paper_id}")


@main.command("mark-reviewed", help="Mark a record reviewed and lock it.")
@click.argument("id_or_alias")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def mark_reviewed(id_or_alias: str, config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    try:
        record = _load_record_by_id(conn, config, id_or_alias)
        record_path_value = db.get_record_path(conn, record.paper_id)
    finally:
        conn.close()

    if record_path_value is None:
        raise click.ClickException(
            f"Record path not found for: {record.paper_id}"
        )

    record_path = resolve_library_path(config.library.root, record_path_value)

    now = utc_now()
    record.status["review"] = "reviewed"
    record.review["locked"] = True
    record.review["reviewed_at"] = now
    record.timestamps["updated_at"] = now

    write_record_atomic(record_path, record)

    conn = db.connect(db_path)
    try:
        db.upsert_paper(conn, record, record_path_value)
    finally:
        conn.close()

    click.echo(f"marked reviewed: {record.handle_id or record.paper_id}")


@main.command("review", help="Interactively edit and lock paper metadata.")
@click.argument("id_or_alias")
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def review(id_or_alias: str, config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)
    logger = _setup_command_logging(config, debug=False)

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    try:
        record = _load_record_by_id(conn, config, id_or_alias)
        record_path_value = db.get_record_path(conn, record.paper_id)
    finally:
        conn.close()

    if record_path_value is None:
        raise click.ClickException(
            f"Record path not found for: {record.paper_id}"
        )

    record_path = resolve_library_path(config.library.root, record_path_value)

    try:
        updated = review_record_interactive(
            record,
            input_func=_click_input,
            output_func=click.echo,
        )
    except ReviewCancelled as exc:
        raise click.ClickException(str(exc)) from exc

    if updated is None:
        return

    write_record_atomic(record_path, updated)
    logger.info(
        "record reviewed: paper_id=%s handle_id=%s",
        updated.paper_id,
        updated.handle_id,
    )

    conn = db.connect(db_path)
    try:
        db.update_record_index(conn, updated, record_path_value)
    finally:
        conn.close()

    click.echo(f"review saved: {updated.handle_id or updated.paper_id}")


@main.command("list", help="List indexed papers.")
@click.option(
    "--needs-review",
    is_flag=True,
    help="Only show papers whose review status is needs_review.",
)
@click.option(
    "--no-handle",
    is_flag=True,
    help="Hide the handle_id column.",
)
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["year", "handle"]),
    default="year",
    show_default=True,
    help="Sort papers by year or handle_id.",
)
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def list_command(
    needs_review: bool, no_handle: bool, sort_by: str, config_path: str
) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    try:
        rows = db.list_papers(conn, needs_review=needs_review, sort=sort_by)
    finally:
        conn.close()

    include_handle = not no_handle
    click.echo(_format_list_header(include_handle=include_handle))
    for row in rows:
        click.echo(_format_list_row(row, include_handle=include_handle))


@main.command("export", help="Export records to an external format.")
@click.option("--bibtex", "fmt", flag_value="bibtex", required=True, help="Export as BibTeX.")
@click.option("--output", "output_path", default=None, help="Path to write output file; default stdout.")
@click.argument("id_or_alias", nargs=-1, required=False)
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def export_command(fmt: str, output_path: str | None, id_or_alias: tuple[str, ...], config_path: str) -> None:
    from paperlib.export import records_to_bibtex
    from paperlib.store.fs import atomic_write_text

    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    conn = db.connect(db_path)
    records = []
    try:
        if id_or_alias:
            for alias in id_or_alias:
                records.append(_load_record_by_id(conn, config, alias))
        else:
            for paper_id, record_path_str in db.list_all_record_paths(conn):
                record_path = Path(record_path_str)
                if not record_path.is_absolute():
                    record_path = config.library.root / record_path
                try:
                    records.append(read_record(record_path))
                except Exception as exc:
                    raise click.ClickException(
                        f"Could not read record {record_path}: {exc}"
                    ) from exc
    finally:
        conn.close()

    bibtex_output = records_to_bibtex(records)

    if output_path:
        atomic_write_text(Path(output_path), bibtex_output)
        click.echo(f"exported {len(records)} records")
    else:
        click.echo(bibtex_output)


@main.command("search", help="Search papers by title, authors, or summary text.")
@click.argument("query")
@click.option(
    "--field",
    type=click.Choice(["title", "authors", "summary", "all"]),
    default="all",
    show_default=True,
    help="Field to search: title, authors, summary, or all.",
)
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["year", "handle"]),
    default="year",
    show_default=True,
    help="Sort results by year or handle_id.",
)
@click.option(
    "--config",
    "config_path",
    default="config.toml",
    show_default=True,
    help=CONFIG_HELP,
)
def search_command(query: str, field: str, sort_by: str, config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

    db_path = config.paths.db
    if not db_path.exists():
        raise click.ClickException(
            "No database found. Run paperlib ingest or paperlib rebuild-index."
        )

    rows: list[dict] = []
    query_lower = query.lower()

    # --- SQLite search for title / authors / all ---
    if field in ("title", "authors", "all"):
        conn = db.connect(db_path)
        try:
            db_rows = db.search_papers(conn, query, sort=sort_by)
        finally:
            conn.close()

        if field == "title":
            # Keep only rows where the title itself matches
            db_rows = [
                r for r in db_rows
                if r.get("title") is not None and query_lower in r["title"].lower()
            ]
        elif field == "authors":
            # Keep only rows where authors_json itself matches
            db_rows = [
                r for r in db_rows
                if r.get("authors_json") is not None
                and query_lower in r["authors_json"].lower()
            ]

        rows.extend(db_rows)

    # --- Summary scan for summary / all ---
    if field in ("summary", "all"):
        existing_ids = {r["paper_id"] for r in rows}
        for json_path in sorted(config.paths.records.glob("*.json")):
            try:
                record = read_record(json_path)
            except Exception:
                continue

            summary_texts = [
                record.summary.get("one_sentence") or "",
                record.summary.get("short") or "",
                record.summary.get("technical") or "",
            ]
            if not any(query_lower in t.lower() for t in summary_texts if t):
                continue

            if record.paper_id in existing_ids:
                continue
            existing_ids.add(record.paper_id)

            title_field = record.metadata.get("title")
            authors_field = record.metadata.get("authors")
            year_field = record.metadata.get("year")

            authors_json_str: str | None = None
            if authors_field is not None and authors_field.value is not None:
                authors_json_str = json.dumps(authors_field.value)

            rows.append({
                "handle_id": record.handle_id,
                "paper_id": record.paper_id,
                "title": title_field.value if title_field is not None else None,
                "authors_json": authors_json_str,
                "year": year_field.value if year_field is not None else None,
                "review_status": record.status.get("review"),
            })

    if not rows:
        click.echo("no results")
        return

    click.echo(_format_list_header(include_handle=True))
    for row in rows:
        click.echo(_format_list_row(row, include_handle=True))


def _print_ingest_report(report) -> None:
    click.echo(f"{'discovered:':<21}{report.discovered}")
    click.echo(f"{'processed:':<21}{report.processed}")
    click.echo(f"{'skipped existing:':<21}{report.skipped_existing}")
    click.echo(f"{'failed:':<21}{report.failed}")
    click.echo(f"{'records written:':<21}{report.records_written}")
    click.echo(f"{'summaries generated:':<21}{report.summaries_generated}")
    click.echo(f"{'summaries failed:':<21}{report.summaries_failed}")
    click.echo(f"{'summaries skipped:':<21}{report.summaries_skipped}")
    click.echo(f"{'locked skipped:':<21}{report.locked_skipped}")
    click.echo(f"{'warnings:':<21}{len(report.warnings)}")
    if report.warnings:
        click.echo("Warnings:")
        for warning in report.warnings:
            click.echo(f"- {warning}")


def _setup_command_logging(config: AppConfig, *, debug: bool) -> logging.Logger:
    logger = setup_logging(config.paths.logs, debug=debug)
    logger.debug("debug logging enabled")
    return logger


def _format_year(value) -> str:
    return "<unknown>" if value is None else str(value)


def _format_list_header(*, include_handle: bool) -> str:
    columns = list(_LIST_COLUMNS)
    if include_handle:
        columns.insert(0, "handle_id")
    return _LIST_SEPARATOR.join(
        column.ljust(_LIST_COLUMN_WIDTHS[column]) for column in columns
    )


def _format_list_row(row: dict, *, include_handle: bool) -> str:
    values = [
        ("paper_id", str(row["paper_id"])),
        ("year", _format_year(row.get("year"))),
        (
            "first_author",
            _truncate_text(_first_author(row.get("authors_json")), 20),
        ),
        ("title", _truncate_title(row.get("title"))),
        ("review_status", row.get("review_status") or ""),
    ]
    if include_handle:
        values.insert(0, ("handle_id", row.get("handle_id") or "<none>"))
    return _LIST_SEPARATOR.join(
        str(value).ljust(_LIST_COLUMN_WIDTHS[column])
        for column, value in values
    )


def _format_show_record(record: dict) -> dict:
    formatted = {
        "handle_id": record.get("handle_id"),
        "paper_id": record.get("paper_id"),
    }
    for key, value in record.items():
        if key not in formatted:
            formatted[key] = value
    return formatted


def _first_author(authors_json) -> str:
    if not authors_json:
        return "<unknown>"
    try:
        authors = json.loads(authors_json)
    except (TypeError, json.JSONDecodeError):
        return "<unknown>"
    if not isinstance(authors, list) or not authors:
        return "<unknown>"
    first = authors[0]
    if not isinstance(first, str) or not first.strip():
        return "<unknown>"
    return first.strip()


def _truncate_title(value) -> str:
    if not value:
        return "<no title>"
    return value if len(value) <= 57 else f"{value[:54]}..."


def _truncate_text(value: str, width: int) -> str:
    return value if len(value) <= width else value[:width]




def _print_dry_run_table(pdfs) -> None:
    click.echo("path | hash16 | size (KB) | pages | validation | reason")
    for pdf in pdfs:
        validation = validate_pdf(pdf.path)
        pages = (
            "-"
            if validation.page_count is None
            else str(validation.page_count)
        )
        validation_status = "ok" if validation.ok else "failed"
        size_kb = pdf.size_bytes // 1024
        click.echo(
            " | ".join(
                [
                    str(pdf.path),
                    pdf.hash16,
                    str(size_kb),
                    pages,
                    validation_status,
                    validation.reason,
                ]
            )
        )


def _click_input(prompt: str) -> str:
    try:
        value = click.prompt(prompt, default="", show_default=False)
    except (click.Abort, EOFError, KeyboardInterrupt) as exc:
        raise KeyboardInterrupt from exc
    if value == "\x03":
        raise KeyboardInterrupt
    return value


def _require_library_root(config: AppConfig) -> None:
    if not config.library.root.exists():
        message = f"Library root does not exist: {config.library.root}"
        raise click.ClickException(message)


def _ensure_runtime_paths(config: AppConfig) -> list[tuple[str, Path, str]]:
    path_specs = [
        ("inbox", config.paths.inbox),
        ("papers", config.paths.papers),
        ("records", config.paths.records),
        ("text", config.paths.text),
        ("db", config.paths.db.parent),
        ("logs", config.paths.logs),
        ("failed", config.paths.failed),
        ("deleted", config.paths.deleted),
        ("duplicates", config.paths.duplicates),
    ]

    statuses = []
    for name, path in path_specs:
        if path.exists():
            state = "ok"
        else:
            path.mkdir(parents=True, exist_ok=True)
            state = "created"
        statuses.append((name, path, state))
    return statuses


if __name__ == "__main__":
    main()
