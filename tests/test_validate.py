from pathlib import Path

import pytest

from paperlib.pipeline import validate


class FakePage:
    def __init__(self, text: str | None):
        self.text = text
        self.extract_count = 0

    def extract_text(self) -> str | None:
        self.extract_count += 1
        return self.text


class FakePDF:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages

    def __enter__(self) -> "FakePDF":
        return self

    def __exit__(self, *args) -> None:
        return None


def test_validate_pdf_valid_with_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "paper.pdf"

    monkeypatch.setattr(
        validate.pdfplumber,
        "open",
        lambda _: FakePDF([FakePage("hello"), FakePage("")]),
    )

    result = validate.validate_pdf(path)

    assert result.path == path
    assert result.ok is True
    assert result.page_count == 2
    assert result.has_text is True
    assert result.reason == "ok"


def test_validate_pdf_unreadable_broken_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "broken.pdf"

    def raise_error(_: Path):
        raise ValueError("broken")

    monkeypatch.setattr(validate.pdfplumber, "open", raise_error)

    result = validate.validate_pdf(path)

    assert result.ok is False
    assert result.page_count is None
    assert result.has_text is False
    assert result.reason == "unreadable PDF: broken"


def test_validate_pdf_zero_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "empty.pdf"
    monkeypatch.setattr(validate.pdfplumber, "open", lambda _: FakePDF([]))

    result = validate.validate_pdf(path)

    assert result.ok is False
    assert result.page_count == 0
    assert result.has_text is False
    assert result.reason == "no pages"


def test_validate_pdf_no_sampled_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "scanned.pdf"
    first = FakePage("  ")
    second = FakePage(None)
    third = FakePage("later")
    monkeypatch.setattr(
        validate.pdfplumber,
        "open",
        lambda _: FakePDF([first, second, third]),
    )

    result = validate.validate_pdf(path)

    assert result.ok is False
    assert result.page_count == 3
    assert result.has_text is False
    assert result.reason == "no text detected in sampled pages"
    assert first.extract_count == 1
    assert second.extract_count == 1
    assert third.extract_count == 0
