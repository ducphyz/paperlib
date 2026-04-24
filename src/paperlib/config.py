from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python 3.11+ includes tomllib.
    raise RuntimeError(
        "tomllib is required. Use Python 3.11 or newer."
    ) from exc

try:
    from dotenv import load_dotenv as _load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency exists after install.
    _load_dotenv = None


@dataclass
class LibraryConfig:
    root: Path


@dataclass
class PathsConfig:
    inbox: Path
    papers: Path
    records: Path
    text: Path
    db: Path
    logs: Path
    failed: Path
    duplicates: Path


@dataclass
class PipelineConfig:
    move_after_ingest: bool
    skip_existing: bool
    dry_run_default: bool


@dataclass
class ExtractionConfig:
    engine: str
    min_char_count: int
    min_word_count: int


@dataclass
class AIConfig:
    enabled: bool
    provider: str
    model: str
    max_tokens: int
    temperature: float
    anthropic_api_key: str | None


@dataclass
class AppConfig:
    library: LibraryConfig
    paths: PathsConfig
    pipeline: PipelineConfig
    extraction: ExtractionConfig
    ai: AIConfig


class ConfigError(ValueError):
    pass


def load_config(config_path: Path | str = "config.toml") -> AppConfig:
    config_file = Path(config_path).expanduser()
    if not config_file.exists():
        raise ConfigError(
            f"Config file not found: {config_file}. "
            "Copy config.example.toml to config.toml."
        )

    _load_env(config_file.parent / ".env")

    with config_file.open("rb") as file:
        data = tomllib.load(file)

    library_data = _section(data, "library")
    root_value = library_data.get("root")
    if not root_value:
        raise ConfigError("Missing required config value: library.root")

    root = Path(root_value).expanduser().resolve()
    paths_data = _section(data, "paths")
    pipeline_data = _section(data, "pipeline")
    extraction_data = _section(data, "extraction")
    ai_data = _section(data, "ai")

    return AppConfig(
        library=LibraryConfig(root=root),
        paths=PathsConfig(
            inbox=_resolve_path(root, paths_data.get("inbox", "inbox")),
            papers=_resolve_path(root, paths_data.get("papers", "papers")),
            records=_resolve_path(root, paths_data.get("records", "records")),
            text=_resolve_path(root, paths_data.get("text", "text")),
            db=_resolve_path(root, paths_data.get("db", "db/library.db")),
            logs=_resolve_path(root, paths_data.get("logs", "logs")),
            failed=_resolve_path(root, paths_data.get("failed", "failed")),
            duplicates=_resolve_path(
                root, paths_data.get("duplicates", "duplicates")
            ),
        ),
        pipeline=PipelineConfig(
            move_after_ingest=bool(
                pipeline_data.get("move_after_ingest", True)
            ),
            skip_existing=bool(pipeline_data.get("skip_existing", True)),
            dry_run_default=bool(
                pipeline_data.get("dry_run_default", False)
            ),
        ),
        extraction=ExtractionConfig(
            engine=str(extraction_data.get("engine", "pdfplumber")),
            min_char_count=int(extraction_data.get("min_char_count", 500)),
            min_word_count=int(extraction_data.get("min_word_count", 100)),
        ),
        ai=AIConfig(
            enabled=bool(ai_data.get("enabled", True)),
            provider=str(ai_data.get("provider", "anthropic")),
            model=str(ai_data.get("model", "claude-sonnet-4-20250514")),
            max_tokens=int(ai_data.get("max_tokens", 1200)),
            temperature=float(ai_data.get("temperature", 0.2)),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        ),
    )


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"Config section must be a table: {name}")
    return value


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _load_env(env_path: Path) -> None:
    if _load_dotenv is not None:
        _load_dotenv(env_path)
        return

    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
