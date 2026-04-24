from __future__ import annotations

from pathlib import Path

import click

from paperlib.config import AppConfig, load_config
from paperlib.models import status as status_values
from paperlib.pipeline.clean import clean_text
from paperlib.pipeline.discover import discover_pdfs
from paperlib.pipeline.extract import extract_text_from_pdf
from paperlib.pipeline.validate import validate_pdf
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
@click.option("--config", "config_path", default="config.toml", show_default=True)
def ingest(dry_run: bool, limit: int | None, config_path: str) -> None:
    if not dry_run and limit is None:
        raise click.ClickException(
            "Phase 3 non-dry-run ingest requires --limit N."
        )

    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

    discovered = discover_pdfs(config.paths.inbox)
    to_process = discovered[:limit] if limit is not None else discovered

    if dry_run:
        _print_dry_run_table(to_process)
        return

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
