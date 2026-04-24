from pathlib import Path
import hashlib

from paperlib.pipeline.discover import discover_pdfs


def test_discover_pdfs_recursively_and_deterministically(tmp_path: Path):
    inbox = tmp_path / "inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)

    first_pdf = inbox / "a.pdf"
    second_pdf = nested / "b.PDF"
    non_pdf = inbox / "note.txt"

    first_data = b"%PDF-1.4 fake a"
    second_data = b"%PDF-1.4 fake b"
    first_pdf.write_bytes(first_data)
    second_pdf.write_bytes(second_data)
    non_pdf.write_text("not a pdf")

    results = discover_pdfs(inbox)

    assert len(results) == 2
    assert [r.path.name for r in results] == ["a.pdf", "b.PDF"]

    expected_hash = hashlib.sha256(first_data).hexdigest()
    first = results[0]
    assert first.path == first_pdf
    assert first.file_hash == expected_hash
    assert first.hash16 == expected_hash[:16]
    assert first.hash8 == expected_hash[:8]
    assert first.size_bytes == len(first_data)
    assert first.modified_time.endswith("Z")

    second_hash = hashlib.sha256(second_data).hexdigest()
    second = results[1]
    assert second.path == second_pdf
    assert second.file_hash == second_hash
    assert second.hash16 == second_hash[:16]
    assert second.hash8 == second_hash[:8]
    assert second.size_bytes == len(second_data)
    assert second.modified_time.endswith("Z")
