from pathlib import Path
import hashlib
import pytest

from paperlib.store.fs import (
    ascii_fold,
    atomic_write_text,
    canonical_pdf_relative_path,
    filename_author_component,
    move_file,
    move_to_deleted,
    move_to_duplicates,
    move_to_failed,
    sanitize_component,
    sha256_file,
)


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "sample.bin"
    data = b"paperlib test data"
    p.write_bytes(data)

    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_ascii_fold_removes_non_ascii_marks():
    assert ascii_fold("José Müller") == "Jose Muller"


def test_sanitize_component_returns_empty_for_falsy_input():
    assert sanitize_component("") == ""
    assert sanitize_component(None) == ""


def test_sanitize_component_folds_lowercases_and_replaces_separators():
    assert sanitize_component(" José / Smith, Jr.; Test: Name ") == (
        "jose_smith_jr_test_name"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Müller", "muller"),
        ("Bøttcher", "bottcher"),
        ("Łukasiewicz", "lukasiewicz"),
        ("Dvořák", "dvorak"),
        ("Ångström", "angstrom"),
        ("Þórsson", "thorsson"),
        ("Ægir", "aegir"),
        ("Straße", "strasse"),
        ("Dıaz", "diaz"),
        ("İstanbul", "istanbul"),
    ],
)
def test_sanitize_component_non_ascii_latin(name, expected):
    assert sanitize_component(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("C. G. L. Bøttcher", "bottcher"),
        ("C G L Bottcher", "bottcher"),
        ("Bøttcher", "bottcher"),
        ("John Smith", "smith"),
        ("A. J. van der Waals", "van_der_waals"),
        ("Maria del Carmen García", "garcia"),
        ("Jean-Luc Picard", "picard"),
        ("", None),
    ],
)
def test_filename_author_component_uses_surname(name, expected):
    assert filename_author_component(name) == expected


def test_sanitize_component_removes_dots_and_other_punctuation():
    assert sanitize_component("Smith Jr.") == "smith_jr"
    assert sanitize_component("A+B=C!") == "abc"


def test_sanitize_component_collapses_and_strips_underscores_hyphens():
    assert sanitize_component("__A   B---") == "a_b"


def test_sanitize_component_truncates_and_strips_suffix():
    assert sanitize_component("alpha beta gamma", max_len=11) == "alpha_beta"


def test_sanitize_component_required_phase4_cases():
    assert sanitize_component("Müller") == "muller"
    assert sanitize_component("van den Berg") == "van_den_berg"
    assert sanitize_component("Smith Jr.") == "smith_jr"
    assert sanitize_component("") == ""
    assert len(sanitize_component("a" * 100)) <= 40
    assert sanitize_component("Cao/Chen:Wang") == "cao_chen_wang"
    assert sanitize_component("  --Smith,, Wang--  ") == "smith_wang"
    assert sanitize_component("Al-InAs 2DEG") == "al-inas_2deg"


def test_canonical_pdf_relative_path_with_known_year_and_author():
    assert canonical_pdf_relative_path(
        year=2024,
        first_author="Smith",
        file_hash="abcdef1234567890",
    ) == "papers/2024/smith_2024_abcdef12.pdf"


def test_canonical_pdf_relative_path_with_unknown_components():
    assert canonical_pdf_relative_path(
        year=None,
        first_author=None,
        file_hash="abcdef1234567890",
    ) == (
        "papers/unknown_year/"
        "unknown_author_unknown_year_abcdef12.pdf"
    )


def test_canonical_pdf_relative_path_sanitizes_author_and_returns_string():
    value = canonical_pdf_relative_path(
        year=2024,
        first_author="Müller",
        file_hash="abcdef1234567890",
    )

    assert value == "papers/2024/muller_2024_abcdef12.pdf"
    assert isinstance(value, str)


def test_canonical_pdf_relative_path_uses_author_surname_only():
    assert canonical_pdf_relative_path(
        year=2024,
        first_author="C. G. L. Bøttcher",
        file_hash="0440c911081cc43b",
    ) == "papers/2024/bottcher_2024_0440c911.pdf"


def test_canonical_pdf_relative_path_empty_author_uses_fallback():
    assert canonical_pdf_relative_path(
        year=2024,
        first_author="",
        file_hash="abcdef1234567890",
    ) == "papers/2024/unknown_author_2024_abcdef12.pdf"


def test_canonical_pdf_relative_path_sanitizes_author_separators():
    assert canonical_pdf_relative_path(
        year=2024,
        first_author="Cao/Chen:Wang",
        file_hash="abcdef1234567890",
    ) == "papers/2024/cao_chen_wang_2024_abcdef12.pdf"


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


def test_move_to_deleted_preserves_name_when_available(tmp_path: Path):
    source = tmp_path / "discard.pdf"
    deleted_dir = tmp_path / "deleted"
    source.write_bytes(b"discard")

    destination = move_to_deleted(source, deleted_dir)

    assert destination == deleted_dir / "discard.pdf"
    assert destination.read_bytes() == b"discard"
    assert not source.exists()


def test_move_to_deleted_adds_deterministic_suffix_on_collision(
    tmp_path: Path,
):
    source = tmp_path / "discard.pdf"
    deleted_dir = tmp_path / "deleted"
    deleted_dir.mkdir()
    source_data = b"incoming"
    source.write_bytes(source_data)
    (deleted_dir / "discard.pdf").write_bytes(b"existing")

    destination = move_to_deleted(source, deleted_dir)

    assert destination == deleted_dir / "discard_1.pdf"
    assert destination.read_bytes() == source_data
    assert (deleted_dir / "discard.pdf").read_bytes() == b"existing"


def test_move_to_duplicates_preserves_name_and_content(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    duplicates_dir = tmp_path / "duplicates"
    source.write_bytes(b"duplicate")

    destination = move_to_duplicates(source, duplicates_dir)

    assert destination == duplicates_dir / "paper.pdf"
    assert destination.read_bytes() == b"duplicate"
    assert not source.exists()


def test_move_file_raises_if_destination_exists(tmp_path: Path):
    source = tmp_path / "source.pdf"
    destination = tmp_path / "dest.pdf"
    source.write_bytes(b"source")
    destination.write_bytes(b"dest")

    with pytest.raises(FileExistsError):
        move_file(source, destination)

    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"dest"
