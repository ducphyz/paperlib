from __future__ import annotations

import re
from datetime import UTC, datetime

from paperlib.models import status as status_values
from paperlib.models.identity import normalize_arxiv_id, normalize_doi
from paperlib.models.metadata import MetadataField


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s]+\b")
_ARXIV_MODERN_RE = re.compile(r"(?:arXiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)")
_ARXIV_OLD_RE = re.compile(r"(?:arXiv:\s*)?([a-z\-]+/\d{7}(?:v\d+)?)")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_YEAR_KEYWORDS = (
    "received",
    "revised",
    "accepted",
    "published",
    "copyright",
    "©",
)


def detect_doi(text: str) -> str | None:
    text = text or ""
    match = _DOI_RE.search(text[:5000])
    if match is None:
        match = _DOI_RE.search(text)
    if match is None:
        return None
    return normalize_doi(match.group(0))


def detect_arxiv_id(text: str, filename: str | None = None) -> str | None:
    text = text or ""
    for candidate in (filename, text[:5000]):
        if not candidate:
            continue
        arxiv_id = _detect_arxiv_in_text(candidate)
        if arxiv_id is not None:
            return arxiv_id
    return None


def detect_year(
    text: str,
    arxiv_id: str | None = None,
    current_year: int | None = None,
) -> tuple[int | None, float | None]:
    if current_year is None:
        current_year = datetime.now(UTC).year

    if arxiv_id is not None:
        match = re.match(r"^(\d{2})\d{2}\.\d{4,5}$", arxiv_id)
        if match is not None:
            yy = int(match.group(1))
            year = 2000 + yy if yy <= current_year % 100 else 1900 + yy
            return year, 0.95

    text = text or ""
    window = text[:2000]
    lower_window = window.lower()
    keyword_spans = [
        (match.start(), match.end())
        for keyword in _YEAR_KEYWORDS
        for match in re.finditer(re.escape(keyword.lower()), lower_window)
    ]

    for year_match in _YEAR_RE.finditer(window):
        year = int(year_match.group(1))
        if not 1900 <= year <= current_year + 1:
            continue
        if any(
            year_match.start() <= end + 80
            and year_match.end() >= start - 80
            for start, end in keyword_spans
        ):
            return year, 0.70

    return None, None


def extract_non_ai_metadata(
    text: str,
    filename: str | None = None,
    current_year: int | None = None,
) -> dict:
    doi = detect_doi(text)
    arxiv_id = detect_arxiv_id(text, filename)
    year, year_confidence = detect_year(
        text, arxiv_id=arxiv_id, current_year=current_year
    )
    return {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "year": year,
        "year_confidence": year_confidence,
    }


def build_non_ai_metadata_fields(
    *,
    year: int | None,
    year_confidence: float | None,
    doi: str | None,
    arxiv_id: str | None,
    now_iso: str | None = None,
) -> dict:
    metadata = {
        "title": MetadataField(),
        "authors": MetadataField(),
        "year": MetadataField(),
        "journal": MetadataField(),
    }
    if year is not None:
        metadata["year"] = MetadataField(
            value=year,
            source=status_values.SOURCE_PDF_TEXT,
            confidence=year_confidence,
            locked=False,
            updated_at=now_iso or _utc_now(),
        )
    return metadata


def _detect_arxiv_in_text(text: str) -> str | None:
    match = _ARXIV_MODERN_RE.search(text)
    if match is not None:
        return normalize_arxiv_id(match.group(1))

    match = _ARXIV_OLD_RE.search(text)
    if match is not None:
        return normalize_arxiv_id(match.group(1))

    return None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
