"""Unit tests for paperlib.export — no CLI, no DB, no filesystem."""
from paperlib.export import record_to_bibtex, records_to_bibtex
from paperlib.models.record import PaperRecord


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _article_record() -> PaperRecord:
    r = PaperRecord(paper_id="p_article", handle_id="article_2024")
    r.identity.doi = "10.1234/test"
    r.metadata["title"].value = "A Great Article"
    r.metadata["authors"].value = ["Smith, J.", "Jones, A."]
    r.metadata["year"].value = 2024
    r.metadata["journal"].value = "Nature Physics"
    return r


def _arxiv_record() -> PaperRecord:
    r = PaperRecord(paper_id="p_arxiv", handle_id="arxiv_2024")
    r.identity.arxiv_id = "2401.12345"
    r.metadata["title"].value = "An arXiv Preprint"
    r.metadata["authors"].value = ["Baker, R."]
    r.metadata["year"].value = 2024
    return r


def _no_ids_record() -> PaperRecord:
    r = PaperRecord(paper_id="p_noids", handle_id="noids_2024")
    r.metadata["title"].value = "An Unknown Paper"
    r.metadata["authors"].value = ["Doe, J."]
    r.metadata["year"].value = 2023
    return r


# ---------------------------------------------------------------------------
# entry type
# ---------------------------------------------------------------------------

def test_doi_record_produces_article_entry():
    result = record_to_bibtex(_article_record())
    assert result.startswith("@article{")


def test_arxiv_only_record_produces_misc_with_eprint():
    result = record_to_bibtex(_arxiv_record())
    assert result.startswith("@misc{")
    assert "eprint" in result
    assert "archivePrefix" in result
    assert "arXiv" in result


def test_neither_doi_nor_arxiv_produces_misc_with_note():
    result = record_to_bibtex(_no_ids_record())
    assert result.startswith("@misc{")
    assert "note" in result
    # underscore in paper_id is escaped per BibTeX rules
    assert r"paperlib:p\_noids" in result


# ---------------------------------------------------------------------------
# cite key
# ---------------------------------------------------------------------------

def test_cite_key_is_handle_id_when_present():
    result = record_to_bibtex(_article_record())
    assert "@article{article_2024," in result


def test_cite_key_falls_back_to_paper_id_when_handle_id_is_none():
    r = _article_record()
    r.handle_id = None
    result = record_to_bibtex(r)
    assert "@article{p_article," in result


# ---------------------------------------------------------------------------
# author formatting
# ---------------------------------------------------------------------------

def test_multiple_authors_joined_with_and():
    result = record_to_bibtex(_article_record())
    assert "Smith, J. and Jones, A." in result


def test_single_author_no_and():
    result = record_to_bibtex(_arxiv_record())
    assert "Baker, R." in result
    assert " and " not in result


# ---------------------------------------------------------------------------
# missing fields
# ---------------------------------------------------------------------------

def test_missing_title_author_year_does_not_raise_and_fields_omitted():
    r = PaperRecord(paper_id="p_empty", handle_id="empty_2024")
    r.identity.doi = "10.1/x"
    # title, authors, year left at default (None)
    result = record_to_bibtex(r)
    assert result.startswith("@article{")
    assert "title" not in result
    assert "author" not in result
    assert "year" not in result


# ---------------------------------------------------------------------------
# BibTeX escaping
# ---------------------------------------------------------------------------

def test_ampersand_escaped():
    r = _no_ids_record()
    r.metadata["title"].value = "A & B"
    result = record_to_bibtex(r)
    assert r"A \& B" in result


def test_percent_escaped():
    r = _no_ids_record()
    r.metadata["title"].value = "100% Sure"
    result = record_to_bibtex(r)
    assert r"100\% Sure" in result


def test_dollar_escaped():
    r = _no_ids_record()
    r.metadata["title"].value = "Cost $10"
    result = record_to_bibtex(r)
    assert r"Cost \$10" in result


def test_hash_escaped():
    r = _no_ids_record()
    r.metadata["title"].value = "Issue #1"
    result = record_to_bibtex(r)
    assert r"Issue \#1" in result


def test_underscore_escaped():
    r = _no_ids_record()
    r.metadata["title"].value = "Some_Word"
    result = record_to_bibtex(r)
    assert r"Some\_Word" in result


# ---------------------------------------------------------------------------
# journal field only for @article
# ---------------------------------------------------------------------------

def test_journal_field_present_for_article():
    result = record_to_bibtex(_article_record())
    assert "journal" in result
    assert "Nature Physics" in result


def test_journal_field_absent_for_arxiv_misc():
    r = _arxiv_record()
    r.metadata["journal"].value = "Nature Physics"
    result = record_to_bibtex(r)
    assert "journal" not in result


# ---------------------------------------------------------------------------
# doi field
# ---------------------------------------------------------------------------

def test_doi_field_present_for_article():
    result = record_to_bibtex(_article_record())
    assert "doi" in result
    assert "10.1234/test" in result


# ---------------------------------------------------------------------------
# records_to_bibtex
# ---------------------------------------------------------------------------

def test_records_to_bibtex_joins_two_entries_with_blank_line():
    r1 = _article_record()
    r2 = _arxiv_record()
    result = records_to_bibtex([r1, r2])
    assert "\n\n" in result
    entries = result.split("\n\n")
    assert len(entries) == 2
    assert entries[0].startswith("@article{")
    assert entries[1].startswith("@misc{")
