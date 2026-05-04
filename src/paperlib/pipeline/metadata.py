from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from paperlib.models import status as status_values
from paperlib.models.identity import normalize_arxiv_id, normalize_doi
from paperlib.models.metadata import MetadataField
from paperlib.utils import utc_now


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
_EMBEDDED_YEAR_CONFIDENCE = 0.65
_EMBEDDED_CONFIDENCE = 0.60
_FILENAME_CONFIDENCE = 0.30
_TITLE_JUNK = {"untitled", "unknown", "none"}
_AUTHOR_JUNK = {"unknown", "anonymous", "none"}
_SOFTWARE_JUNK = (
    "microsoft word",
    "latex",
    "pdftex",
    "acrobat",
    "adobe",
    "ghostscript",
    "quartz pdfcontext",
    "wkhtmltopdf",
)
_FILENAME_JUNK_TOKENS = {
    "arxiv",
    "copy",
    "download",
    "downloaded",
    "draft",
    "final",
    "paper",
}
_FILENAME_ARXIV_RE = re.compile(
    r"(?:^|[\s_-])arxiv[\s_:-]*(\d{4}\.\d{4,5}(?:v\d+)?)$",
    re.IGNORECASE,
)
_FILENAME_YEAR_AUTHOR_TITLE_RE = re.compile(
    r"^(?P<year>19\d{2}|20\d{2})\s+-\s+"
    r"(?P<author>[^-]+?)\s+-\s+(?P<title>.+)$"
)
_FILENAME_AUTHOR_YEAR_TITLE_RE = re.compile(
    r"^(?P<author>[A-Za-z][A-Za-z.' -]{1,60}?)"
    r"(?P<year>19\d{2}|20\d{2})[_ -]+(?P<title>.+)$"
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


def extract_title_from_pdf_metadata(pdf_metadata: dict | None) -> str | None:
    value = _metadata_string(pdf_metadata, "/Title", "Title", "title")
    if value is None:
        return None

    title = _collapse_whitespace(value)
    lower_title = title.lower()
    if (
        not title
        or lower_title in _TITLE_JUNK
        or len(title) <= 1
        or lower_title.endswith(".pdf")
        or lower_title.startswith("microsoft word -")
        or _is_software_junk(lower_title)
    ):
        return None
    return title


def extract_authors_from_pdf_metadata(
    pdf_metadata: dict | None,
) -> list[str] | None:
    value = _metadata_string(pdf_metadata, "/Author", "Author", "authors")
    if value is None:
        return None

    author_text = _collapse_whitespace(value)
    lower_author = author_text.lower()
    if (
        not author_text
        or lower_author in _AUTHOR_JUNK
        or _is_software_junk(lower_author)
    ):
        return None

    authors = [
        fragment
        for fragment in _split_author_text(author_text)
        if _valid_author_fragment(fragment)
    ]
    return authors or None


def extract_year_from_pdf_metadata(pdf_metadata: dict | None) -> int | None:
    for key in (
        "/CreationDate",
        "CreationDate",
        "creation_date",
        "/PublicationDate",
        "PublicationDate",
        "publication_date",
        "/ModDate",
        "ModDate",
        "/Year",
        "Year",
        "year",
    ):
        value = _metadata_value(pdf_metadata, key)
        year = _extract_valid_year(value)
        if year is not None:
            return year
    return None


def parse_filename_metadata(filename: str | None) -> dict:
    result = {"title": None, "authors": None, "year": None, "arxiv_id": None}
    if not filename:
        return result

    stem = Path(filename).name
    stem = stem[: -len(Path(stem).suffix)] if Path(stem).suffix else stem
    stem = _collapse_whitespace(stem.replace("_", " "))
    if not stem or stem.lower() in _FILENAME_JUNK_TOKENS:
        return result

    arxiv_match = _FILENAME_ARXIV_RE.search(stem)
    if arxiv_match is not None:
        result["arxiv_id"] = arxiv_match.group(1)
        return result

    match = _FILENAME_YEAR_AUTHOR_TITLE_RE.match(stem)
    if match is None:
        match = _FILENAME_AUTHOR_YEAR_TITLE_RE.match(stem)
    if match is None:
        return result

    year = _extract_valid_year(match.group("year"))
    author = _clean_filename_author(match.group("author"))
    title = _clean_filename_title(match.group("title"))
    if year is None:
        return result

    result["year"] = year
    if author is not None:
        result["authors"] = [author]
    result["title"] = title
    return result


def build_non_ai_metadata_fields(
    *,
    year: int | None,
    year_confidence: float | None,
    doi: str | None,
    arxiv_id: str | None,
    embedded_pdf_metadata: dict | None = None,
    original_filename: str | None = None,
    now_iso: str | None = None,
) -> dict:
    metadata = {
        "title": MetadataField(),
        "authors": MetadataField(),
        "year": MetadataField(),
        "journal": MetadataField(),
    }
    timestamp = now_iso or utc_now()

    embedded_title = extract_title_from_pdf_metadata(embedded_pdf_metadata)
    embedded_authors = extract_authors_from_pdf_metadata(embedded_pdf_metadata)
    embedded_year = extract_year_from_pdf_metadata(embedded_pdf_metadata)
    filename_metadata = parse_filename_metadata(original_filename)

    title_source = None
    title_confidence = None
    title = embedded_title
    if title is not None:
        title_source = status_values.SOURCE_PDF_EMBEDDED_META
        title_confidence = _EMBEDDED_CONFIDENCE
    elif filename_metadata["title"] is not None:
        title = filename_metadata["title"]
        title_source = status_values.SOURCE_FILENAME
        title_confidence = _FILENAME_CONFIDENCE
    if title is not None:
        metadata["title"] = MetadataField(
            value=title,
            source=title_source,
            confidence=title_confidence,
            locked=False,
            updated_at=timestamp,
        )

    authors_source = None
    authors_confidence = None
    authors = embedded_authors
    if authors is not None:
        authors_source = status_values.SOURCE_PDF_EMBEDDED_META
        authors_confidence = _EMBEDDED_CONFIDENCE
    elif filename_metadata["authors"] is not None:
        authors = filename_metadata["authors"]
        authors_source = status_values.SOURCE_FILENAME
        authors_confidence = _FILENAME_CONFIDENCE
    if authors is not None:
        metadata["authors"] = MetadataField(
            value=authors,
            source=authors_source,
            confidence=authors_confidence,
            locked=False,
            updated_at=timestamp,
        )

    if year is not None:
        metadata["year"] = MetadataField(
            value=year,
            source=status_values.SOURCE_PDF_TEXT,
            confidence=year_confidence,
            locked=False,
            updated_at=timestamp,
        )
    elif embedded_year is not None:
        metadata["year"] = MetadataField(
            value=embedded_year,
            source=status_values.SOURCE_PDF_EMBEDDED_META,
            confidence=_EMBEDDED_YEAR_CONFIDENCE,
            locked=False,
            updated_at=timestamp,
        )
    elif filename_metadata["year"] is not None:
        metadata["year"] = MetadataField(
            value=filename_metadata["year"],
            source=status_values.SOURCE_FILENAME,
            confidence=_FILENAME_CONFIDENCE,
            locked=False,
            updated_at=timestamp,
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


def _metadata_string(pdf_metadata: dict | None, *keys: str) -> str | None:
    value = None
    for key in keys:
        value = _metadata_value(pdf_metadata, key)
        if value is not None:
            break
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _metadata_value(pdf_metadata: dict | None, key: str):
    if not isinstance(pdf_metadata, dict):
        return None
    return pdf_metadata.get(key)


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_software_junk(lower_value: str) -> bool:
    return any(
        lower_value == software or lower_value.startswith(f"{software} ")
        for software in _SOFTWARE_JUNK
    )


def _split_author_text(author_text: str) -> list[str]:
    if ";" in author_text:
        return [_collapse_whitespace(part) for part in author_text.split(";")]

    if re.search(r"\s+and\s+", author_text, flags=re.IGNORECASE):
        return [
            _collapse_whitespace(part)
            for part in re.split(r"\s+and\s+", author_text, flags=re.IGNORECASE)
        ]

    if "," in author_text:
        parts = [_collapse_whitespace(part) for part in author_text.split(",")]
        if len(parts) >= 3 or all(_looks_like_full_author(part) for part in parts):
            return parts

    return [author_text]


def _looks_like_full_author(value: str) -> bool:
    return bool(re.search(r"\s", value) or re.search(r"\b[A-Z]\.", value))


def _valid_author_fragment(value: str) -> bool:
    lower_value = value.lower()
    return bool(
        value
        and lower_value not in _AUTHOR_JUNK
        and not _is_software_junk(lower_value)
    )


def _extract_valid_year(value) -> int | None:
    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        match = re.search(r"(19\d{2}|20\d{2})", value)
        if match is None:
            return None
        year = int(match.group(1))
    else:
        return None

    current_year = datetime.now(UTC).year
    if 1900 <= year <= current_year + 1:
        return year
    return None


def _clean_filename_title(value: str) -> str | None:
    title = _collapse_whitespace(value.replace("_", " ").replace("-", " "))
    lower_title = title.lower()
    if not title or lower_title in _FILENAME_JUNK_TOKENS:
        return None

    words = [
        word
        for word in title.split()
        if word.lower().strip("._-") not in _FILENAME_JUNK_TOKENS
    ]
    title = " ".join(words)
    if not title or title.lower() in _FILENAME_JUNK_TOKENS:
        return None
    return title


def _clean_filename_author(value: str) -> str | None:
    author = _collapse_whitespace(value.replace("_", " "))
    lower_author = author.lower()
    if (
        not author
        or lower_author in _FILENAME_JUNK_TOKENS
        or not _valid_author_fragment(author)
        or re.search(r"\d", author)
    ):
        return None
    return author
