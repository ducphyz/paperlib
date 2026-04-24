from pathlib import Path

import pytest

from paperlib.models import status
from paperlib.pipeline import extract


class FakePage:
    def __init__(self, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error

    def extract_text(self) -> str | None:
        if self.error is not None:
            raise self.error
        return self.text


class FakePDF:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages

    def __enter__(self) -> "FakePDF":
        return self

    def __exit__(self, *args) -> None:
        return None


def _mock_pdf(monkeypatch: pytest.MonkeyPatch, pages: list[FakePage]) -> None:
    monkeypatch.setattr(extract.pdfplumber, "open", lambda _: FakePDF(pages))


def test_extract_text_success_multiple_pages(monkeypatch: pytest.MonkeyPatch):
    path = Path("paper.pdf")
    _mock_pdf(
        monkeypatch,
        [FakePage("alpha beta gamma"), FakePage("delta epsilon zeta")],
    )

    result = extract.extract_text_from_pdf(
        path, min_char_count=10, min_word_count=5
    )

    assert result.path == path
    assert result.status == status.EXTRACTION_OK
    assert result.raw_text == "alpha beta gamma\n\ndelta epsilon zeta"
    assert result.page_count == 2
    assert result.char_count == len(result.raw_text)
    assert result.word_count == len(result.raw_text.split())
    assert result.quality == status.QUALITY_GOOD


def test_extract_text_word_count_zero_is_scanned(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_pdf(monkeypatch, [FakePage("")])

    result = extract.extract_text_from_pdf(
        Path("paper.pdf"), min_char_count=1, min_word_count=1
    )

    assert result.quality == status.QUALITY_SCANNED


def test_extract_text_char_count_below_min_is_low_text(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_pdf(monkeypatch, [FakePage("short text")])

    result = extract.extract_text_from_pdf(
        Path("paper.pdf"), min_char_count=100, min_word_count=1
    )

    assert result.quality == status.QUALITY_LOW_TEXT


def test_extract_text_word_count_below_min_is_low_text(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_pdf(monkeypatch, [FakePage("one two three four five")])

    result = extract.extract_text_from_pdf(
        Path("paper.pdf"), min_char_count=1, min_word_count=100
    )

    assert result.quality == status.QUALITY_LOW_TEXT


def test_extract_text_replacement_char_ratio_is_equation_heavy(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_pdf(monkeypatch, [FakePage("alpha beta gamma �")])

    result = extract.extract_text_from_pdf(
        Path("paper.pdf"), min_char_count=1, min_word_count=1
    )

    assert result.quality == status.QUALITY_EQUATION_HEAVY


def test_extract_text_open_exception_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
):
    def raise_error(_: Path):
        raise ValueError("broken")

    monkeypatch.setattr(extract.pdfplumber, "open", raise_error)

    result = extract.extract_text_from_pdf(
        Path("broken.pdf"), min_char_count=1, min_word_count=1
    )

    assert result.status == status.EXTRACTION_FAILED
    assert result.warnings == ["pdfplumber failed: broken"]


def test_extract_text_one_page_exception_returns_partial(
    monkeypatch: pytest.MonkeyPatch,
):
    _mock_pdf(
        monkeypatch,
        [
            FakePage("first page"),
            FakePage(error=ValueError("bad page")),
            FakePage("third page"),
        ],
    )

    result = extract.extract_text_from_pdf(
        Path("paper.pdf"), min_char_count=1, min_word_count=1
    )

    assert result.status == status.EXTRACTION_PARTIAL
    assert result.raw_text == "first page\n\n\n\nthird page"
    assert result.page_count == 3
    assert result.warnings == ["page 2: bad page"]
