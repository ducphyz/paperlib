from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class ValidationResult:
    path: Path
    ok: bool
    page_count: int | None
    has_text: bool
    reason: str


def validate_pdf(path: Path) -> ValidationResult:
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                return ValidationResult(
                    path=path,
                    ok=False,
                    page_count=page_count,
                    has_text=False,
                    reason="no pages",
                )

            has_text = any(
                (page.extract_text() or "").strip()
                for page in pdf.pages[:2]
            )
            if not has_text:
                return ValidationResult(
                    path=path,
                    ok=False,
                    page_count=page_count,
                    has_text=False,
                    reason="no text detected in sampled pages",
                )

            return ValidationResult(
                path=path,
                ok=True,
                page_count=page_count,
                has_text=True,
                reason="ok",
            )
    except Exception as exc:
        return ValidationResult(
            path=path,
            ok=False,
            page_count=None,
            has_text=False,
            reason=f"unreadable PDF: {exc}",
        )
