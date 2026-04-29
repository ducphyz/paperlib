from pathlib import Path
import shutil
import tomllib

from click.testing import CliRunner

from paperlib.cli import main


REPO_ROOT = Path(__file__).parents[1]


def test_changelog_exists_and_documents_v1_1():
    changelog = REPO_ROOT / "CHANGELOG.md"

    assert changelog.exists()
    text = changelog.read_text(encoding="utf-8")
    assert "v1.1" in text
    assert "0.1.1" in text
    assert "handle_id" in text
    assert "SQLite schema" in text


def test_readme_documents_v1_1_workflows():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    for required in (
        "JSON records",
        "SQLite",
        "paper_id",
        "handle_id",
        "rebuild-index",
        "mark-reviewed",
        "paperlib review",
        "review.locked",
        "anthropic:",
        "openai:",
        "openrouter:",
        "openai-compat:",
        "Known Limitations",
        "0.1.1",
    ):
        assert required in text


def test_config_example_parses_and_validates():
    config_path = REPO_ROOT / "config.example.toml"
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert data["library"]["root"] == "."
    assert "openai:gpt-4o" in config_path.read_text(encoding="utf-8")
    assert "openrouter:" in config_path.read_text(encoding="utf-8")
    assert "openai-compat:" in config_path.read_text(encoding="utf-8")

    runtime_dirs = [
        REPO_ROOT / "inbox",
        REPO_ROOT / "papers",
        REPO_ROOT / "records",
        REPO_ROOT / "text",
        REPO_ROOT / "db",
        REPO_ROOT / "logs",
        REPO_ROOT / "failed",
        REPO_ROOT / "duplicates",
    ]
    preexisting = {path for path in runtime_dirs if path.exists()}
    try:
        result = CliRunner().invoke(
            main, ["validate-config", "--config", str(config_path)]
        )
        assert result.exit_code == 0
        assert "Library root:" in result.output
    finally:
        for path in reversed(runtime_dirs):
            if path not in preexisting and path.exists():
                shutil.rmtree(path)


def test_cli_help_and_version_still_pass():
    help_result = CliRunner().invoke(main, ["--help"])
    version_result = CliRunner().invoke(main, ["--version"])

    assert help_result.exit_code == 0
    assert "paperlib" in help_result.output
    assert version_result.exit_code == 0
    assert "paperlib" in version_result.output
