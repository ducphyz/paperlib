"""Unit tests for paperlib.pipeline.lookup.

All network calls are monkeypatched via paperlib.pipeline.lookup._http_get.
"""
from __future__ import annotations

import json

import pytest

import paperlib.pipeline.lookup as lookup_mod
from paperlib.config import LookupConfig
from paperlib.models.metadata import MetadataField
from paperlib.models.record import PaperRecord
from paperlib.models.status import SOURCE_ARXIV_API, SOURCE_CROSSREF
from paperlib.pipeline.lookup import lookup_metadata


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

NOW = "2026-01-01T00:00:00Z"

CROSSREF_JSON = json.dumps({
    "message": {
        "title": ["Quantum Electrodynamics"],
        "author": [
            {"given": "Richard", "family": "Feynman"},
            {"given": "Julian", "family": "Schwinger"},
        ],
        "published": {"date-parts": [[1949]]},
        "container-title": ["Physical Review"],
    }
}).encode()

ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>General Relativity
    and Curved Spacetime</title>
    <published>1915-11-25T00:00:00Z</published>
    <author><name>Albert Einstein</name></author>
    <author><name>Marcel Grossmann</name></author>
  </entry>
</feed>"""


def _record_with_doi(doi: str = "10.1103/PhysRev.76.749") -> PaperRecord:
    r = PaperRecord(paper_id="p_test", handle_id="test_1949")
    r.identity.doi = doi
    return r


def _record_with_arxiv(arxiv_id: str = "1503.02912") -> PaperRecord:
    r = PaperRecord(paper_id="p_test", handle_id="test_2015")
    r.identity.arxiv_id = arxiv_id
    return r


def _record_with_both(doi: str = "10.1103/x", arxiv_id: str = "1503.02912") -> PaperRecord:
    r = PaperRecord(paper_id="p_test", handle_id="test_2024")
    r.identity.doi = doi
    r.identity.arxiv_id = arxiv_id
    return r


def _enabled_config(**kw) -> LookupConfig:
    defaults = dict(enabled=True, mailto=None, timeout_sec=5.0)
    defaults.update(kw)
    return LookupConfig(**defaults)


# ---------------------------------------------------------------------------
# Crossref tests
# ---------------------------------------------------------------------------

def test_crossref_fills_title_authors_year_journal(monkeypatch):
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: CROSSREF_JSON)
    record = _record_with_doi()
    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert result.metadata["title"].value == "Quantum Electrodynamics"
    assert result.metadata["title"].source == SOURCE_CROSSREF
    assert result.metadata["title"].confidence == 0.9
    assert "Richard Feynman" in result.metadata["authors"].value
    assert result.metadata["year"].value == 1949
    assert result.metadata["journal"].value == "Physical Review"


def test_crossref_does_not_overwrite_locked_title_field(monkeypatch):
    """If title.locked=True and title.value=None, the API result is skipped for title."""
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: CROSSREF_JSON)
    record = _record_with_doi()
    record.metadata["title"] = MetadataField(value=None, locked=True)

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    # title still None because field is locked
    assert result.metadata["title"].value is None
    # other fields (authors, year) should be filled since they're not locked
    assert result.metadata["authors"].value is not None


def test_crossref_returns_record_unchanged_and_error_on_network_error(monkeypatch):
    def raises_network(url, *, headers, timeout):
        raise OSError("Connection refused")

    monkeypatch.setattr(lookup_mod, "_http_get", raises_network)
    record = _record_with_doi()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is not None
    assert "Crossref" in err
    assert result.metadata["title"].value is None


def test_crossref_returns_record_unchanged_and_error_on_http_non_200(monkeypatch):
    import urllib.error

    def raises_http(url, *, headers, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(lookup_mod, "_http_get", raises_http)
    record = _record_with_doi()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is not None
    assert "Crossref" in err
    assert result.metadata["title"].value is None


def test_crossref_returns_record_unchanged_and_error_on_malformed_json(monkeypatch):
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: b"not json {{")
    record = _record_with_doi()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is not None
    assert "Crossref" in err
    assert result.metadata["title"].value is None


# ---------------------------------------------------------------------------
# arXiv tests
# ---------------------------------------------------------------------------

def test_arxiv_fills_title_authors_year(monkeypatch):
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: ARXIV_XML)
    record = _record_with_arxiv()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert result.metadata["title"].value == "General Relativity and Curved Spacetime"
    assert result.metadata["title"].source == SOURCE_ARXIV_API
    assert "Albert Einstein" in result.metadata["authors"].value
    assert result.metadata["year"].value == 1915


def test_arxiv_returns_record_unchanged_and_error_on_malformed_xml(monkeypatch):
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: b"<not valid xml><<")
    record = _record_with_arxiv()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is not None
    assert "arXiv" in err
    assert result.metadata["title"].value is None


# ---------------------------------------------------------------------------
# Guard / early-exit tests
# ---------------------------------------------------------------------------

def test_no_http_call_if_lookup_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(lookup_mod, "_http_get", lambda *a, **kw: called.append(1))
    record = _record_with_doi()

    result, err = lookup_metadata(record, LookupConfig(enabled=False), NOW)

    assert err is None
    assert called == []
    assert result.metadata["title"].value is None


def test_no_http_call_if_title_and_authors_already_populated(monkeypatch):
    called = []
    monkeypatch.setattr(lookup_mod, "_http_get", lambda *a, **kw: called.append(1))
    record = _record_with_doi()
    record.metadata["title"].value = "Already Known Title"
    record.metadata["authors"].value = ["Ada Lovelace"]

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert called == []
    assert result.metadata["title"].value == "Already Known Title"


def test_lookup_runs_if_title_present_but_authors_missing(monkeypatch):
    monkeypatch.setattr(lookup_mod, "_http_get", lambda url, *, headers, timeout: CROSSREF_JSON)
    record = _record_with_doi()
    record.metadata["title"].value = "Already Known Title"

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert result.metadata["title"].value == "Already Known Title"
    assert result.metadata["authors"].value is not None


def test_no_http_call_if_no_doi_and_no_arxiv_id(monkeypatch):
    called = []
    monkeypatch.setattr(lookup_mod, "_http_get", lambda *a, **kw: called.append(1))
    record = PaperRecord(paper_id="p_empty")
    # no DOI, no arXiv ID

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert called == []


def test_crossref_fills_title_arxiv_not_called(monkeypatch):
    """When Crossref fills the title, arXiv must not be called."""
    called_urls: list[str] = []

    def mock_http(url, *, headers, timeout):
        called_urls.append(url)
        return CROSSREF_JSON

    monkeypatch.setattr(lookup_mod, "_http_get", mock_http)
    record = _record_with_both()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None
    assert result.metadata["title"].value == "Quantum Electrodynamics"
    # Only one HTTP call (Crossref); arXiv URL never hit
    assert len(called_urls) == 1
    assert "crossref.org" in called_urls[0]


def test_crossref_fails_arxiv_called_as_fallback(monkeypatch):
    """If Crossref raises, arXiv is tried next and fills the title."""
    call_count = [0]

    def mock_http(url, *, headers, timeout):
        call_count[0] += 1
        if "crossref" in url:
            raise OSError("Crossref down")
        return ARXIV_XML

    monkeypatch.setattr(lookup_mod, "_http_get", mock_http)
    record = _record_with_both()

    result, err = lookup_metadata(record, _enabled_config(), NOW)

    assert err is None  # arXiv succeeded, so no error
    assert call_count[0] == 2  # both Crossref and arXiv called
    assert result.metadata["title"].value == "General Relativity and Curved Spacetime"
    assert result.metadata["title"].source == SOURCE_ARXIV_API
