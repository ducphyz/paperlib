"""Crossref / arXiv metadata lookup to fill missing fields after text extraction."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from paperlib.__about__ import __version__
from paperlib.config import LookupConfig
from paperlib.models.metadata import MetadataField
from paperlib.models.record import PaperRecord
from paperlib.models.status import SOURCE_ARXIV_API, SOURCE_CROSSREF

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


# ---------------------------------------------------------------------------
# HTTP helper — a single call-site makes monkeypatching trivial in tests.
# ---------------------------------------------------------------------------

def _http_get(url: str, *, headers: dict[str, str], timeout: float) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_metadata(
    record: PaperRecord,
    config: LookupConfig,
    now_iso: str,
) -> tuple[PaperRecord, str | None]:
    """Fill missing metadata fields from Crossref and/or arXiv.

    Returns ``(record, error_message_or_None)``.  The record is updated
    in-place on a successful lookup; returned unchanged if all lookups fail.
    """
    # --- early-exit guards ---
    if not config.enabled:
        return record, None
    if (
        record.metadata["title"].value is not None
        and record.metadata["authors"].value is not None
    ):
        return record, None
    if record.identity.doi is None and record.identity.arxiv_id is None:
        return record, None

    # --- Crossref (DOI) ---
    crossref_error: str | None = None
    if record.identity.doi is not None:
        try:
            result = _crossref_lookup(record.identity.doi, config=config)
            _apply_result(record, result, source=SOURCE_CROSSREF, now_iso=now_iso)
        except Exception as exc:  # noqa: BLE001
            crossref_error = f"Crossref lookup failed: {exc}"

    # --- arXiv fallback (only when title still not filled) ---
    if record.identity.arxiv_id is not None and record.metadata["title"].value is None:
        try:
            result = _arxiv_lookup(record.identity.arxiv_id, config=config)
            _apply_result(record, result, source=SOURCE_ARXIV_API, now_iso=now_iso)
            crossref_error = None  # arXiv filled the gap
        except Exception as exc:  # noqa: BLE001
            arxiv_err = f"arXiv lookup failed: {exc}"
            if crossref_error:
                return record, f"{crossref_error}; {arxiv_err}"
            return record, arxiv_err

    if crossref_error:
        return record, crossref_error

    return record, None


# ---------------------------------------------------------------------------
# Provider-specific lookups
# ---------------------------------------------------------------------------

def _crossref_lookup(doi: str, *, config: LookupConfig) -> dict[str, object]:
    """Return a dict of metadata fields from Crossref for *doi*."""
    encoded = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{encoded}"

    if config.mailto:
        user_agent = f"paperlib/{__version__} (mailto:{config.mailto})"
    else:
        user_agent = f"paperlib/{__version__}"

    raw = _http_get(url, headers={"User-Agent": user_agent}, timeout=config.timeout_sec)
    data = json.loads(raw)
    msg = data["message"]

    title_list = msg.get("title") or []
    title: str | None = title_list[0] if title_list else None

    raw_authors = msg.get("author") or []
    authors: list[str] | None = None
    if raw_authors:
        authors = [
            f"{a.get('given', '')} {a['family']}".strip()
            for a in raw_authors
            if "family" in a
        ] or None

    year: int | None = None
    published = msg.get("published") or {}
    date_parts = published.get("date-parts") or []
    if date_parts and date_parts[0]:
        year = int(date_parts[0][0])

    journal: str | None = None
    container_titles = msg.get("container-title") or []
    if container_titles:
        journal = container_titles[0]

    return {"title": title, "authors": authors, "year": year, "journal": journal}


def _arxiv_lookup(arxiv_id: str, *, config: LookupConfig) -> dict[str, object]:
    """Return a dict of metadata fields from the arXiv Atom API for *arxiv_id*."""
    url = f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    raw = _http_get(url, headers={}, timeout=config.timeout_sec)

    root = ET.fromstring(raw)
    entry = root.find(f"{_ATOM_NS}entry")
    if entry is None:
        raise ValueError(f"No entry element in arXiv response for {arxiv_id}")

    title_el = entry.find(f"{_ATOM_NS}title")
    title: str | None = None
    if title_el is not None and title_el.text:
        title = " ".join(title_el.text.split())  # normalise whitespace / newlines

    author_els = entry.findall(f"{_ATOM_NS}author/{_ATOM_NS}name")
    authors: list[str] | None = None
    if author_els:
        authors = [el.text for el in author_els if el.text] or None

    year: int | None = None
    published_el = entry.find(f"{_ATOM_NS}published")
    if published_el is not None and published_el.text:
        year = int(published_el.text[:4])

    return {"title": title, "authors": authors, "year": year}


# ---------------------------------------------------------------------------
# Field application
# ---------------------------------------------------------------------------

def _apply_result(
    record: PaperRecord,
    result: dict[str, object],
    *,
    source: str,
    now_iso: str,
) -> None:
    """Write API result fields into *record*, respecting per-field locks."""
    for key, value in result.items():
        if key not in record.metadata:
            continue
        # Skip empty / falsy values
        if value is None or (isinstance(value, (str, list)) and not value):
            continue
        # Respect field-level lock
        if record.metadata[key].locked:
            continue
        if _field_exists(record.metadata[key].value):
            continue
        record.metadata[key] = MetadataField(
            value=value,
            source=source,
            confidence=0.9,
            locked=False,
            updated_at=now_iso,
        )


def _field_exists(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True
