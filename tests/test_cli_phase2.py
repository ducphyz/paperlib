from pathlib import Path

from click.testing import CliRunner

from paperlib.cli import main


def _write_config(path: Path, root: Path) -> None:
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
enabled = false
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2
"""
    )


def _relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*")}


def test_ingest_dry_run_prints_header_without_creating_files(tmp_path: Path):
    library_root = tmp_path / "library"
    inbox = library_root / "inbox"
    inbox.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)
    before = _relative_files(library_root)

    runner = CliRunner()
    result = runner.invoke(
        main, ["ingest", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "path | hash16 | size (KB) | pages | validation | reason" in result.output
    assert _relative_files(library_root) == before
