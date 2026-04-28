from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from paperlib.config import AppConfig, load_config
from paperlib.logging_config import setup_logging
from paperlib.pipeline.discover import discover_pdfs
from paperlib.pipeline.ingest import ingest_library
from paperlib.pipeline.validate import validate_pdf
from paperlib.store import db
from paperlib.store.json_store import read_record_dict


@click.group()
def main() -> None:
    """paperlib command line interface."""
    pass


@main.command("validate-config")
@click.option("--config", "config_path", default="config.toml", show_default=True)
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
        click.echo("ANTHROPIC_API_KEY: missing, non-AI commands still work")
    elif config.ai.enabled:
        click.echo("ANTHROPIC_API_KEY: present")
    else:
        click.echo("ANTHROPIC_API_KEY: not required")


@main.command("ingest")
@click.option("--dry-run", is_flag=True)
@click.option("--limit", type=int, default=None)
@click.option("--no-ai", is_flag=True)
@click.option("--debug", is_flag=True)
@click.option("--config", "config_path", default="config.toml", show_default=True)
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


@main.command("rebuild-index")
@click.option("--dry-run", is_flag=True)
@click.option("--no-backfill", is_flag=True)
@click.option("--debug", is_flag=True)
@click.option("--config", "config_path", default="config.toml", show_default=True)
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


@main.command("status")
@click.option("--config", "config_path", default="config.toml", show_default=True)
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


@main.command("show")
@click.argument("id_or_alias")
@click.option("--config", "config_path", default="config.toml", show_default=True)
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
        try:
            paper_id = db.resolve_id(conn, id_or_alias)
        except db.IdNotFound as exc:
            raise click.ClickException(str(exc)) from exc
        record_path_value = db.get_record_path(conn, paper_id)
    finally:
        conn.close()

    if record_path_value is None:
        raise click.ClickException(f"Record path not found for: {paper_id}")

    record_path = Path(record_path_value)
    if not record_path.is_absolute():
        record_path = config.library.root / record_path

    try:
        record = read_record_dict(record_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        json.dumps(_format_show_record(record), indent=2, ensure_ascii=False)
    )


@main.command("list")
@click.option("--needs-review", is_flag=True)
@click.option("--no-handle", is_flag=True)
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["year", "handle"]),
    default="year",
    show_default=True,
)
@click.option("--config", "config_path", default="config.toml", show_default=True)
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

    columns = ["paper_id", "year", "first_author", "title", "review_status"]
    if not no_handle:
        columns.insert(0, "handle_id")
    click.echo(" | ".join(columns))
    for row in rows:
        values = [
            row["paper_id"],
            _format_year(row.get("year")),
            _first_author(row.get("authors_json")),
            _truncate_title(row.get("title")),
            row.get("review_status") or "",
        ]
        if not no_handle:
            values.insert(0, row.get("handle_id") or "<none>")
        click.echo(" | ".join(values))


def _print_ingest_report(report) -> None:
    click.echo(f"{'discovered:':<21}{report.discovered}")
    click.echo(f"{'processed:':<21}{report.processed}")
    click.echo(f"{'skipped existing:':<21}{report.skipped_existing}")
    click.echo(f"{'failed:':<21}{report.failed}")
    click.echo(f"{'records written:':<21}{report.records_written}")
    click.echo(f"{'summaries generated:':<21}{report.summaries_generated}")
    click.echo(f"{'summaries failed:':<21}{report.summaries_failed}")
    click.echo(f"{'summaries skipped:':<21}{report.summaries_skipped}")
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
    return value if len(value) <= 60 else f"{value[:57]}..."


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
