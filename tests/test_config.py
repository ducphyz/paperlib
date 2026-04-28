from __future__ import annotations

from pathlib import Path

import pytest

from paperlib.ai.client import OPENROUTER_DEFAULT_BASE_URL
from paperlib.config import ConfigError, load_config


def _write_config(path: Path, root: Path, ai_block: str) -> None:
    path.write_text(
        f"""
[library]
root = "{root}"

[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
duplicates = "duplicates"

[pipeline]
move_after_ingest = true
skip_existing = true
dry_run_default = false

[extraction]
engine = "pdfplumber"
min_char_count = 500
min_word_count = 100

[ai]
{ai_block}
""",
        encoding="utf-8",
    )


def test_old_anthropic_config_still_loads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2
""",
    )

    config = load_config(config_path)

    assert config.ai.model == "claude-sonnet-4-20250514"
    assert config.ai.base_url is None
    assert config.ai.api_key_env == "ANTHROPIC_API_KEY"
    assert config.ai.anthropic_api_key == "test-key"


def test_openai_config_loads_with_default_api_key_env(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
model = "openai:gpt-4o"
max_tokens = 1200
temperature = 0.2
""",
    )

    config = load_config(config_path)

    assert config.ai.model == "openai:gpt-4o"
    assert config.ai.base_url is None
    assert config.ai.api_key_env == "OPENAI_API_KEY"


def test_openrouter_gets_default_base_url(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
model = "openrouter:meta-llama/llama-3.3-70b-instruct"
max_tokens = 1200
temperature = 0.2
""",
    )

    config = load_config(config_path)

    assert config.ai.base_url == OPENROUTER_DEFAULT_BASE_URL
    assert config.ai.api_key_env == "OPENROUTER_API_KEY"


def test_openai_compat_without_base_url_fails_validation(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
model = "openai-compat:local-model"
max_tokens = 1200
temperature = 0.2
""",
    )

    with pytest.raises(ConfigError, match="base_url"):
        load_config(config_path)


def test_openai_compat_with_base_url_and_custom_api_key_env_loads(
    tmp_path: Path,
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
model = "openai-compat:local-model"
base_url = "http://localhost:11434/v1"
api_key_env = "LOCAL_AI_KEY"
max_tokens = 1200
temperature = 0.2
""",
    )

    config = load_config(config_path)

    assert config.ai.base_url == "http://localhost:11434/v1"
    assert config.ai.api_key_env == "LOCAL_AI_KEY"


def test_unknown_model_prefix_fails_validation(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(
        config_path,
        root,
        """
enabled = true
model = "other:model"
max_tokens = 1200
temperature = 0.2
""",
    )

    with pytest.raises(ConfigError, match="Unknown AI model provider prefix"):
        load_config(config_path)
