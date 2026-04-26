from __future__ import annotations

from pathlib import Path

import click

from paperlib.config import AppConfig, load_config
from paperlib.models import status as status_values
from paperlib.pipeline.clean import clean_text
from paperlib.pipeline.discover import discover_pdfs
from paperlib.pipeline.extract import extract_text_from_pdf
from paperlib.pipeline.ingest import ingest_library
from paperlib.pipeline.validate import validate_pdf
from paperlib.store import db
from paperlib.store.fs import atomic_write_text, move_to_failed


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
@click.option("--config", "config_path", default="config.toml", show_default=True)
def ingest(
    dry_run: bool, limit: int | None, no_ai: bool, config_path: str
) -> None:
    if not dry_run and not no_ai and limit is None:
        raise click.ClickException(
            "Phase 3 non-dry-run ingest requires --limit N."
        )

    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

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

    if no_ai:
        report = ingest_library(
            config,
            limit=limit,
            dry_run=False,
            no_ai=True,
        )
        _print_ingest_report(report)
        return

    _run_phase3_ingest(config, limit)


@main.command("rebuild-index")
@click.option("--config", "config_path", default="config.toml", show_default=True)
def rebuild_index(config_path: str) -> None:
    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)
    result = db.rebuild_index_from_records(config.paths.db, config.paths.records)

    click.echo(f"records loaded: {result['records_loaded']}")
    click.echo(f"records skipped: {result['records_skipped']}")
    click.echo(f"JSON errors encountered: {result['json_errors']}")
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


def _print_ingest_report(report) -> None:
    click.echo(f"discovered: {report.discovered}")
    click.echo(f"processed: {report.processed}")
    click.echo(f"skipped_existing: {report.skipped_existing}")
    click.echo(f"failed: {report.failed}")
    click.echo(f"records_written: {report.records_written}")
    click.echo(f"summaries_skipped: {report.summaries_skipped}")
    if report.warnings:
        click.echo("warnings:")
        for warning in report.warnings:
            click.echo(f"- {warning}")


def _run_phase3_ingest(config: AppConfig, limit: int | None) -> None:
    discovered = discover_pdfs(config.paths.inbox)
    to_process = discovered[:limit] if limit is not None else discovered
    _ensure_runtime_paths(config)

    written = 0
    failed = 0

    click.echo("path | validation | extraction | quality | output")
    for pdf in to_process:
        validation = validate_pdf(pdf.path)
        if not validation.ok:
            failed_path = move_to_failed(pdf.path, config.paths.failed)
            failed += 1
            click.echo(
                " | ".join(
                    [
                        str(pdf.path),
                        "failed",
                        "-",
                        "-",
                        str(failed_path),
                    ]
                )
            )
            continue

        extraction = extract_text_from_pdf(
            pdf.path,
            min_char_count=config.extraction.min_char_count,
            min_word_count=config.extraction.min_word_count,
        )
        if extraction.status == status_values.EXTRACTION_FAILED:
            failed += 1
            output = "-"
        else:
            text_path = config.paths.text / f"{pdf.hash16}.txt"
            atomic_write_text(text_path, clean_text(extraction.raw_text))
            written += 1
            output = str(text_path)

        click.echo(
            " | ".join(
                [
                    str(pdf.path),
                    "ok",
                    extraction.status,
                    extraction.quality,
                    output,
                ]
            )
        )

    click.echo(
        f"discovered={len(discovered)} processed={len(to_process)} "
        f"written={written} failed={failed}"
    )


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
