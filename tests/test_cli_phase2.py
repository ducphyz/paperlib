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


def _write_minimal_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    words = " ".join(f"word{i}" for i in range(140))
    text = (
        "arXiv:2401.12345 Published 12 March 2024 "
        f"DOI 10.1234/phase2 {words}"
    )
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        ),
    ]

    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(content)


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
    assert _relative_files(library_root) - before == {
        Path("logs"),
        Path("logs/ingest.log"),
    }
    assert not (library_root / "records").exists()
    assert not (library_root / "text").exists()
    assert not (library_root / "db" / "library.db").exists()


def test_ingest_writes_non_empty_log_file(tmp_path: Path):
    library_root = tmp_path / "library"
    inbox = library_root / "inbox"
    inbox.mkdir(parents=True)
    _write_minimal_pdf(inbox / "paper.pdf")
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)

    result = CliRunner().invoke(
        main, ["ingest", "--no-ai", "--config", str(config_path)]
    )

    log_path = library_root / "logs" / "ingest.log"
    assert result.exit_code == 0
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert log_text
    assert "INFO paperlib.pipeline.ingest: ingest started" in log_text
    assert "INFO paperlib.pipeline.ingest: ingest finished" in log_text


def test_ingest_debug_flag_writes_debug_entries(tmp_path: Path):
    library_root = tmp_path / "library"
    (library_root / "inbox").mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    _write_config(config_path, library_root)

    result = CliRunner().invoke(
        main, ["ingest", "--dry-run", "--debug", "--config", str(config_path)]
    )

    log_text = (library_root / "logs" / "ingest.log").read_text(
        encoding="utf-8"
    )
    assert result.exit_code == 0
    assert "DEBUG paperlib: debug logging enabled" in log_text
