from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperlib.ai.client import (
    AIError,
    OPENAI_COMPAT_PROVIDER,
    OPENROUTER_DEFAULT_BASE_URL,
    OPENROUTER_PROVIDER,
    default_api_key_env,
    split_model_string,
)

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python 3.14.3 includes tomllib.
    raise RuntimeError(
        "tomllib is required. Use Python 3.14.3."
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
    deleted: Path
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
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass
class LookupConfig:
    enabled: bool = False
    mailto: str | None = None
    timeout_sec: float = 5.0


@dataclass
class AppConfig:
    library: LibraryConfig
    paths: PathsConfig
    pipeline: PipelineConfig
    extraction: ExtractionConfig
    ai: AIConfig
    lookup: LookupConfig = field(default_factory=LookupConfig)


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
    ai_config = _load_ai_config(ai_data)
    lookup_data = _section(data, "lookup")
    lookup_config = LookupConfig(
        enabled=bool(lookup_data.get("enabled", False)),
        mailto=_optional_str(lookup_data.get("mailto")),
        timeout_sec=float(lookup_data.get("timeout_sec", 5.0)),
    )

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
            deleted=_resolve_path(
                root, paths_data.get("deleted", "deleted")
            ),
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
        ai=ai_config,
        lookup=lookup_config,
    )


def _load_ai_config(ai_data: dict[str, Any]) -> AIConfig:
    model = str(ai_data.get("model", "claude-sonnet-4-20250514"))
    try:
        provider, _provider_model = split_model_string(model)
    except AIError as exc:
        raise ConfigError(str(exc)) from exc

    base_url = _optional_str(ai_data.get("base_url"))
    if provider == OPENROUTER_PROVIDER and base_url is None:
        base_url = OPENROUTER_DEFAULT_BASE_URL
    if provider == OPENAI_COMPAT_PROVIDER and base_url is None:
        raise ConfigError("openai-compat model requires ai.base_url")

    api_key_env = _optional_str(ai_data.get("api_key_env"))
    if api_key_env is None:
        api_key_env = default_api_key_env(provider)

    return AIConfig(
        enabled=bool(ai_data.get("enabled", True)),
        provider=str(ai_data.get("provider", provider)),
        model=model,
        max_tokens=int(ai_data.get("max_tokens", 1200)),
        temperature=float(ai_data.get("temperature", 0.2)),
        anthropic_api_key=os.getenv(api_key_env),
        base_url=base_url,
        api_key_env=api_key_env,
    )


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"Config section must be a table: {name}")
    return value


def _optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
