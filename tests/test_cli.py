from pathlib import Path

from click.testing import CliRunner

from paperlib.cli import main
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.validate import ValidationResult


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


def test_ingest_requires_dry_run(tmp_path: Path):
    runner = CliRunner()

    result = runner.invoke(
        main, ["ingest", "--config", str(tmp_path / "config.toml")]
    )

    assert result.exit_code != 0
    assert "Only ingest --dry-run is implemented in Phase 2." in result.output


def test_ingest_dry_run_prints_discovery_validation_table(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    discovered = DiscoveredPDF(
        path=root / "inbox" / "paper.pdf",
        file_hash="a" * 64,
        hash16="a" * 16,
        hash8="a" * 8,
        size_bytes=2048,
        modified_time="2026-04-25T12:34:56Z",
    )

    monkeypatch.setattr(
        "paperlib.cli.discover_pdfs",
        lambda inbox_path: [discovered],
    )
    monkeypatch.setattr(
        "paperlib.cli.validate_pdf",
        lambda path: ValidationResult(
            path=path,
            ok=True,
            page_count=3,
            has_text=True,
            reason="ok",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        main, ["ingest", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert "path | hash16 | size (KB) | pages | validation | reason" in result.output
    assert f"{discovered.path} | {discovered.hash16} | 2 | 3 | ok | ok" in result.output
