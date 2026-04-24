from pathlib import Path
import hashlib

from paperlib.store.fs import atomic_write_text, move_to_failed, sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "sample.bin"
    data = b"paperlib test data"
    p.write_bytes(data)

    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_atomic_write_text_writes_expected_utf8_content(tmp_path: Path):
    path = tmp_path / "text.txt"

    atomic_write_text(path, "hello ﬁ")

    assert path.read_text(encoding="utf-8") == "hello ﬁ"


def test_atomic_write_text_creates_parent_directories(tmp_path: Path):
    path = tmp_path / "nested" / "text.txt"

    atomic_write_text(path, "hello")

    assert path.exists()


def test_atomic_write_text_leaves_no_temporary_file_on_success(
    tmp_path: Path,
):
    path = tmp_path / "nested" / "text.txt"

    atomic_write_text(path, "hello")

    assert list(path.parent.glob("*.tmp")) == []


def test_atomic_write_text_replaces_existing_file(tmp_path: Path):
    path = tmp_path / "text.txt"
    path.write_text("old", encoding="utf-8")

    atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"


def test_move_to_failed_preserves_name_when_available(tmp_path: Path):
    source = tmp_path / "bad.pdf"
    failed_dir = tmp_path / "failed"
    source.write_bytes(b"bad")

    destination = move_to_failed(source, failed_dir)

    assert destination == failed_dir / "bad.pdf"
    assert destination.read_bytes() == b"bad"
    assert not source.exists()


def test_move_to_failed_adds_deterministic_suffix_on_collision(
    tmp_path: Path,
):
    source = tmp_path / "bad.pdf"
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    source_data = b"incoming"
    source.write_bytes(source_data)
    (failed_dir / "bad.pdf").write_bytes(b"existing")

    destination = move_to_failed(source, failed_dir)

    assert destination == failed_dir / "bad_1.pdf"
    assert destination.read_bytes() == source_data
    assert (failed_dir / "bad.pdf").read_bytes() == b"existing"


def test_move_to_failed_increments_collision_suffix(tmp_path: Path):
    source = tmp_path / "bad.pdf"
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    source.write_bytes(b"incoming")
    (failed_dir / "bad.pdf").write_bytes(b"existing")
    (failed_dir / "bad_1.pdf").write_bytes(b"existing 1")

    destination = move_to_failed(source, failed_dir)

    assert destination == failed_dir / "bad_2.pdf"
