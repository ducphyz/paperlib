from pathlib import Path

from click.testing import CliRunner

from paperlib.models import status
from paperlib.cli import main
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.extract import ExtractionResult
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


def test_ingest_non_dry_run_requires_limit(tmp_path: Path):
    runner = CliRunner()

    result = runner.invoke(
        main, ["ingest", "--config", str(tmp_path / "config.toml")]
    )

    assert result.exit_code != 0
    assert "Phase 3 non-dry-run ingest requires --limit N." in result.output


def test_ingest_limit_writes_cleaned_text(tmp_path: Path, monkeypatch):
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
            page_count=1,
            has_text=True,
            reason="ok",
        ),
    )
    monkeypatch.setattr(
        "paperlib.cli.extract_text_from_pdf",
        lambda path, *, min_char_count, min_word_count: ExtractionResult(
            path=path,
            status=status.EXTRACTION_OK,
            engine="pdfplumber",
            engine_version="test",
            page_count=1,
            char_count=8,
            word_count=2,
            quality=status.QUALITY_GOOD,
            warnings=[],
            raw_text="  hello\t\tworld  ",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        main, ["ingest", "--limit", "1", "--config", str(config_path)]
    )

    text_path = root / "text" / f"{discovered.hash16}.txt"
    assert result.exit_code == 0
    assert text_path.read_text(encoding="utf-8") == "hello world"
    assert "path | validation | extraction | quality | output" in result.output
    assert "discovered=1 processed=1 written=1 failed=0" in result.output


def test_ingest_moves_invalid_pdf_to_failed(tmp_path: Path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    pdf_path = inbox / "bad.pdf"
    pdf_path.write_bytes(b"broken")
    discovered = DiscoveredPDF(
        path=pdf_path,
        file_hash="b" * 64,
        hash16="b" * 16,
        hash8="b" * 8,
        size_bytes=6,
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
            ok=False,
            page_count=None,
            has_text=False,
            reason="broken",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        main, ["ingest", "--limit", "1", "--config", str(config_path)]
    )

    failed_path = root / "failed" / "bad.pdf"
    assert result.exit_code == 0
    assert failed_path.read_bytes() == b"broken"
    assert not pdf_path.exists()
    assert "discovered=1 processed=1 written=0 failed=1" in result.output
