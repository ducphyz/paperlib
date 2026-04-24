from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from paperlib.models import status as status_values


@dataclass(frozen=True)
class ExtractionResult:
    path: Path
    status: str
    engine: str
    engine_version: str
    page_count: int
    char_count: int
    word_count: int
    quality: str
    warnings: list[str]
    raw_text: str


def extract_text_from_pdf(
    path: Path, *, min_char_count: int, min_word_count: int
) -> ExtractionResult:
    engine = "pdfplumber"
    engine_version = getattr(pdfplumber, "__version__", "unknown")
    warnings: list[str] = []

    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            page_texts = []
            failed_pages = 0

            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    page_texts.append(page.extract_text() or "")
                except Exception as exc:
                    failed_pages += 1
                    page_texts.append("")
                    warnings.append(f"page {page_number}: {exc}")

            if page_count > 0 and failed_pages == page_count:
                raw_text = ""
                return _result(
                    path=path,
                    status=status_values.EXTRACTION_FAILED,
                    engine=engine,
                    engine_version=engine_version,
                    page_count=page_count,
                    warnings=warnings or ["all page extraction failed"],
                    raw_text=raw_text,
                    min_char_count=min_char_count,
                    min_word_count=min_word_count,
                )

            raw_text = "\n\n".join(page_texts)
            result_status = (
                status_values.EXTRACTION_PARTIAL
                if failed_pages
                else status_values.EXTRACTION_OK
            )
            return _result(
                path=path,
                status=result_status,
                engine=engine,
                engine_version=engine_version,
                page_count=page_count,
                warnings=warnings,
                raw_text=raw_text,
                min_char_count=min_char_count,
                min_word_count=min_word_count,
            )
    except Exception as exc:
        return _result(
            path=path,
            status=status_values.EXTRACTION_FAILED,
            engine=engine,
            engine_version=engine_version,
            page_count=0,
            warnings=[f"pdfplumber failed: {exc}"],
            raw_text="",
            min_char_count=min_char_count,
            min_word_count=min_word_count,
        )


def _result(
    *,
    path: Path,
    status: str,
    engine: str,
    engine_version: str,
    page_count: int,
    warnings: list[str],
    raw_text: str,
    min_char_count: int,
    min_word_count: int,
) -> ExtractionResult:
    char_count = len(raw_text)
    word_count = len(raw_text.split())
    return ExtractionResult(
        path=path,
        status=status,
        engine=engine,
        engine_version=engine_version,
        page_count=page_count,
        char_count=char_count,
        word_count=word_count,
        quality=_classify_quality(
            raw_text=raw_text,
            char_count=char_count,
            word_count=word_count,
            min_char_count=min_char_count,
            min_word_count=min_word_count,
        ),
        warnings=list(warnings),
        raw_text=raw_text,
    )


def _classify_quality(
    *,
    raw_text: str,
    char_count: int,
    word_count: int,
    min_char_count: int,
    min_word_count: int,
) -> str:
    if word_count == 0:
        return status_values.QUALITY_SCANNED
    if char_count < min_char_count:
        return status_values.QUALITY_LOW_TEXT
    if word_count < min_word_count:
        return status_values.QUALITY_LOW_TEXT
    if raw_text.count("\ufffd") / max(char_count, 1) > 0.05:
        return status_values.QUALITY_EQUATION_HEAVY
    return status_values.QUALITY_GOOD
