from __future__ import annotations

from pathlib import Path

import click

from paperlib.config import AppConfig, load_config
from paperlib.pipeline.discover import discover_pdfs
from paperlib.pipeline.validate import validate_pdf


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
@click.option("--config", "config_path", default="config.toml", show_default=True)
def ingest(dry_run: bool, config_path: str) -> None:
    if not dry_run:
        raise click.ClickException(
            "Only ingest --dry-run is implemented in Phase 2."
        )

    try:
        config = load_config(config_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    _require_library_root(config)

    discovered = discover_pdfs(config.paths.inbox)

    click.echo("path | hash16 | size (KB) | pages | validation | reason")
    for pdf in discovered:
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
