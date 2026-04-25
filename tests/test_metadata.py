from paperlib.pipeline.metadata import (
    build_non_ai_metadata_fields,
    detect_arxiv_id,
    detect_doi,
    detect_year,
    extract_non_ai_metadata,
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
