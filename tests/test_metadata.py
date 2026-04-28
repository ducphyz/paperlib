from paperlib.models import status as status_values
from paperlib.pipeline.metadata import (
    build_non_ai_metadata_fields,
    detect_arxiv_id,
    detect_doi,
    detect_year,
    extract_authors_from_pdf_metadata,
    extract_non_ai_metadata,
    extract_title_from_pdf_metadata,
    extract_year_from_pdf_metadata,
    parse_filename_metadata,
)


def test_detect_doi_required_cases():
    assert (
        detect_doi("DOI: 10.1103/PhysRevLett.123.456")
        == "10.1103/physrevlett.123.456"
    )
    assert (
        detect_doi("See https://doi.org/10.1000/ABC.DEF.")
        == "10.1000/abc.def"
    )
    assert detect_doi("No identifier here.") is None
    assert detect_doi(("x" * 5001) + " 10.1000/LATE.DOI") == "10.1000/late.doi"


def test_detect_arxiv_id_required_cases():
    assert (
        detect_arxiv_id("", filename="paper_arXiv_2401.12345v2.pdf")
        == "2401.12345"
    )
    assert detect_arxiv_id("see arXiv:2401.12345v2") == "2401.12345"
    assert detect_arxiv_id("see cond-mat/0211034v3") == "cond-mat/0211034"
    assert detect_arxiv_id("No arxiv identifier.") is None
    assert (
        detect_arxiv_id(
            "text has arXiv:2401.99999",
            filename="paper_arXiv_2401.12345v2.pdf",
        )
        == "2401.12345"
    )


def test_detect_year_from_arxiv_id_required_cases():
    assert detect_year("", arxiv_id="2401.12345", current_year=2026) == (
        2024,
        0.95,
    )
    assert detect_year("", arxiv_id="9908.12345", current_year=2026) == (
        1999,
        0.95,
    )
    assert detect_year("", arxiv_id="2601.12345", current_year=2026) == (
        2026,
        0.95,
    )


def test_detect_year_keyword_heuristic_required_cases():
    assert detect_year("Received 12 March 2022", current_year=2026) == (
        2022,
        0.70,
    )
    assert detect_year(
        "References [1] 1998 [2] 2005 [3] 2024",
        current_year=2026,
    ) == (None, None)
    assert detect_year(
        "Published " + ("x" * 40) + " 2024",
        current_year=2026,
    ) == (2024, 0.70)
    assert detect_year("Published 2099", current_year=2026) == (None, None)


def test_extract_non_ai_metadata_required_cases():
    result = extract_non_ai_metadata(
        "Published 2024. DOI 10.1000/ABC.DEF. arXiv:2401.12345v2",
        current_year=2026,
    )

    assert set(result) == {"doi", "arxiv_id", "year", "year_confidence"}
    assert result == {
        "doi": "10.1000/abc.def",
        "arxiv_id": "2401.12345",
        "year": 2024,
        "year_confidence": 0.95,
    }
    assert "title" not in result
    assert "authors" not in result
    assert "journal" not in result


def test_build_non_ai_metadata_fields_populates_only_detected_year():
    metadata = build_non_ai_metadata_fields(
        year=2024,
        year_confidence=0.70,
        doi="10.1000/test",
        arxiv_id="2401.12345",
        now_iso="2026-04-25T00:00:00Z",
    )

    assert set(metadata) == {"title", "authors", "year", "journal"}
    assert metadata["title"].to_dict() == {
        "value": None,
        "source": None,
        "confidence": None,
        "locked": False,
        "updated_at": None,
    }
    assert metadata["authors"].value is None
    assert metadata["journal"].value is None
    assert metadata["year"].to_dict() == {
        "value": 2024,
        "source": "pdf_text",
        "confidence": 0.70,
        "locked": False,
        "updated_at": "2026-04-25T00:00:00Z",
    }


def test_build_non_ai_metadata_fields_leaves_year_null_when_unknown():
    metadata = build_non_ai_metadata_fields(
        year=None,
        year_confidence=None,
        doi="10.1000/test",
        arxiv_id="2401.12345",
        now_iso="2026-04-25T00:00:00Z",
    )

    assert metadata["year"].to_dict() == {
        "value": None,
        "source": None,
        "confidence": None,
        "locked": False,
        "updated_at": None,
    }


def test_build_non_ai_metadata_fields_uses_full_embedded_metadata():
    metadata = build_non_ai_metadata_fields(
        year=None,
        year_confidence=None,
        doi=None,
        arxiv_id=None,
        embedded_pdf_metadata={
            "/Title": "  Microwave   Response  ",
            "/Author": "A. Smith; B. Jones",
            "/CreationDate": "D:20140301000000Z",
        },
        original_filename="download.pdf",
        now_iso="2026-04-25T00:00:00Z",
    )

    assert metadata["title"].value == "Microwave Response"
    assert metadata["title"].source == status_values.SOURCE_PDF_EMBEDDED_META
    assert metadata["title"].confidence == 0.60
    assert metadata["title"].locked is False
    assert metadata["authors"].value == ["A. Smith", "B. Jones"]
    assert metadata["authors"].source == status_values.SOURCE_PDF_EMBEDDED_META
    assert metadata["year"].value == 2014
    assert metadata["year"].source == status_values.SOURCE_PDF_EMBEDDED_META
    assert metadata["year"].locked is False


def test_build_non_ai_metadata_fields_allows_filename_partial_fallbacks():
    title_only = build_non_ai_metadata_fields(
        year=None,
        year_confidence=None,
        doi=None,
        arxiv_id=None,
        embedded_pdf_metadata={"/Title": "Embedded Title"},
        original_filename="2014 - Smith - Microwave Response.pdf",
        now_iso="2026-04-25T00:00:00Z",
    )
    authors_only = build_non_ai_metadata_fields(
        year=None,
        year_confidence=None,
        doi=None,
        arxiv_id=None,
        embedded_pdf_metadata={"/Author": "A. Smith and B. Jones"},
        original_filename="2014 - Smith - Microwave Response.pdf",
        now_iso="2026-04-25T00:00:00Z",
    )

    assert title_only["title"].value == "Embedded Title"
    assert title_only["authors"].value == ["Smith"]
    assert title_only["authors"].source == status_values.SOURCE_FILENAME
    assert title_only["year"].value == 2014
    assert authors_only["title"].value == "Microwave Response"
    assert authors_only["title"].source == status_values.SOURCE_FILENAME
    assert authors_only["authors"].value == ["A. Smith", "B. Jones"]


def test_embedded_pdf_metadata_junk_titles_are_rejected():
    for title in (
        "untitled",
        "unknown",
        "none",
        "paper.pdf",
        "Microsoft Word",
        "Microsoft Word - something",
    ):
        assert extract_title_from_pdf_metadata({"/Title": title}) is None


def test_embedded_pdf_metadata_junk_authors_are_rejected():
    for author in (
        "unknown",
        "anonymous",
        "none",
        "Microsoft Word",
        "LaTeX",
        "pdfTeX",
        "Acrobat",
        "Adobe",
    ):
        assert extract_authors_from_pdf_metadata({"/Author": author}) is None


def test_embedded_pdf_metadata_future_year_is_rejected():
    assert extract_year_from_pdf_metadata({"CreationDate": "D:20990101"}) is None


def test_filename_metadata_supported_patterns():
    assert parse_filename_metadata("2014 - Smith - Microwave Response.pdf") == {
        "title": "Microwave Response",
        "authors": ["Smith"],
        "year": 2014,
        "arxiv_id": None,
    }
    assert parse_filename_metadata("Smith2014_Microwave_Response.pdf") == {
        "title": "Microwave Response",
        "authors": ["Smith"],
        "year": 2014,
        "arxiv_id": None,
    }
    assert parse_filename_metadata("arXiv-2401.12345v2.pdf") == {
        "title": None,
        "authors": None,
        "year": None,
        "arxiv_id": "2401.12345v2",
    }
    assert parse_filename_metadata("download.pdf") == {
        "title": None,
        "authors": None,
        "year": None,
        "arxiv_id": None,
    }
    assert parse_filename_metadata("paper.pdf") == {
        "title": None,
        "authors": None,
        "year": None,
        "arxiv_id": None,
    }


def test_existing_arxiv_year_takes_precedence_over_embedded_year():
    metadata = build_non_ai_metadata_fields(
        year=2024,
        year_confidence=0.95,
        doi=None,
        arxiv_id="2401.12345",
        embedded_pdf_metadata={"CreationDate": "D:20190101"},
        original_filename="2018 - Smith - Title.pdf",
        now_iso="2026-04-25T00:00:00Z",
    )

    assert metadata["year"].value == 2024
    assert metadata["year"].source == status_values.SOURCE_PDF_TEXT
    assert metadata["year"].confidence == 0.95
