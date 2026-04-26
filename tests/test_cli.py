from pathlib import Path

from click.testing import CliRunner

from paperlib.cli import main
from paperlib.pipeline.discover import DiscoveredPDF
from paperlib.pipeline.ingest import IngestReport
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


def test_ingest_dry_run_prints_table_and_forces_no_ai(
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
    calls = []

    monkeypatch.setattr(
        "paperlib.cli.discover_pdfs",
        lambda inbox_path: [discovered],
    )
    monkeypatch.setattr(
        "paperlib.cli.validate_pdf",
        lambda path: ValidationResult(path, True, 3, True, "ok"),
    )

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(discovered=1, processed=1)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        ["ingest", "--dry-run", "--limit", "1", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "path | hash16 | size (KB) | pages | validation | reason" in result.output
    assert (
        f"{discovered.path} | {discovered.hash16} | 2 | 3 | ok | ok"
        in result.output
    )
    assert calls == [(1, True, True)]


def test_ingest_no_ai_routes_to_ingest_library_without_ai(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(discovered=2, processed=2, summaries_skipped=2)

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main,
        ["ingest", "--no-ai", "--limit", "2", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert calls == [(2, False, True)]
    assert "summaries_skipped: 2" in result.output
    assert "warnings: 0" in result.output


def test_plain_ingest_routes_to_ingest_library_with_ai_mode(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    calls = []

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        calls.append((limit, dry_run, no_ai))
        return IngestReport(
            discovered=1,
            processed=1,
            records_written=1,
            summaries_generated=1,
        )

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(
        main, ["ingest", "--limit", "1", "--config", str(config_path)]
    )

    assert result.exit_code == 0
    assert calls == [(1, False, False)]
    assert "records_written: 1" in result.output
    assert "summaries_generated: 1" in result.output


def test_ingest_report_prints_all_fields_and_warnings(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)

    def fake_ingest_library(config, *, limit, dry_run, no_ai):
        return IngestReport(
            discovered=3,
            processed=2,
            skipped_existing=1,
            failed=0,
            records_written=2,
            summaries_generated=1,
            summaries_failed=1,
            summaries_skipped=0,
            warnings=["AIError: unavailable"],
        )

    monkeypatch.setattr("paperlib.cli.ingest_library", fake_ingest_library)

    result = CliRunner().invoke(main, ["ingest", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "discovered: 3" in result.output
    assert "processed: 2" in result.output
    assert "skipped_existing: 1" in result.output
    assert "failed: 0" in result.output
    assert "records_written: 2" in result.output
    assert "summaries_generated: 1" in result.output
    assert "summaries_failed: 1" in result.output
    assert "summaries_skipped: 0" in result.output
    assert "warnings: 1" in result.output
    assert "- AIError: unavailable" in result.output
