from pathlib import Path

from click.testing import CliRunner

from paperlib.cli import main
from paperlib.models import status
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


def _discovered(path: Path, hash16: str = "a" * 16) -> DiscoveredPDF:
    return DiscoveredPDF(
        path=path,
        file_hash=hash16 + ("0" * (64 - len(hash16))),
        hash16=hash16,
        hash8=hash16[:8],
        size_bytes=8,
        modified_time="2026-04-25T12:34:56Z",
    )


def test_ingest_dry_run_writes_no_text_and_moves_no_files(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    pdf_path = inbox / "paper.pdf"
    pdf_path.write_bytes(b"fake pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    monkeypatch.setattr(
        "paperlib.cli.discover_pdfs",
        lambda inbox_path: [_discovered(pdf_path)],
    )
    monkeypatch.setattr(
        "paperlib.cli.validate_pdf",
        lambda path: ValidationResult(path, True, 1, True, "ok"),
    )
    monkeypatch.setattr(
        "paperlib.cli.extract_text_from_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not extract")
        ),
    )

    result = CliRunner().invoke(
        main, ["ingest", "--dry-run", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert pdf_path.exists()
    assert not (root / "text").exists()
    assert not (root / "failed").exists()


def test_ingest_non_dry_run_without_limit_exits_nonzero():
    result = CliRunner().invoke(main, ["ingest"])

    assert result.exit_code != 0
    assert "Phase 3 non-dry-run ingest requires --limit N." in result.output


def test_ingest_limit_one_extracts_cleans_and_writes_one_text_file(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    first = _discovered(root / "inbox" / "first.pdf", "a" * 16)
    second = _discovered(root / "inbox" / "second.pdf", "b" * 16)
    extracted_paths = []

    monkeypatch.setattr(
        "paperlib.cli.discover_pdfs",
        lambda inbox_path: [first, second],
    )
    monkeypatch.setattr(
        "paperlib.cli.validate_pdf",
        lambda path: ValidationResult(path, True, 1, True, "ok"),
    )

    def fake_extract(path: Path, *, min_char_count: int, min_word_count: int):
        extracted_paths.append(path)
        return ExtractionResult(
            path=path,
            status=status.EXTRACTION_OK,
            engine="pdfplumber",
            engine_version="test",
            page_count=1,
            char_count=10,
            word_count=2,
            quality=status.QUALITY_GOOD,
            warnings=[],
            raw_text="  hello\t\tworld  ",
        )

    monkeypatch.setattr("paperlib.cli.extract_text_from_pdf", fake_extract)

    result = CliRunner().invoke(
        main, ["ingest", "--limit", "1", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert extracted_paths == [first.path]
    assert (root / "text" / f"{first.hash16}.txt").read_text() == "hello world"
    assert not (root / "text" / f"{second.hash16}.txt").exists()
