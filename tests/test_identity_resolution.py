from paperlib.models.identity import (
    build_aliases,
    normalize_arxiv_id,
    normalize_doi,
)


def test_normalize_doi_required_cases():
    assert (
        normalize_doi("10.1103/PhysRevLett.123.456")
        == "10.1103/physrevlett.123.456"
    )
    assert (
        normalize_doi("https://doi.org/10.1103/PhysRevLett.123.456")
        == "10.1103/physrevlett.123.456"
    )
    assert (
        normalize_doi("http://dx.doi.org/10.1000/ABC.DEF")
        == "10.1000/abc.def"
    )
    assert normalize_doi("doi:10.1000/ABC.DEF.") == "10.1000/abc.def"
    assert (
        normalize_doi("https://doi.org/10.1103/PhysRevLett.123.456)")
        == "10.1103/physrevlett.123.456"
    )
    assert normalize_doi(None) is None
    assert normalize_doi("  ") is None


def test_normalize_arxiv_id_required_cases():
    assert normalize_arxiv_id("2401.12345") == "2401.12345"
    assert normalize_arxiv_id("arXiv:2401.12345") == "2401.12345"
    assert normalize_arxiv_id("arXiv:2401.12345v2") == "2401.12345"
    assert normalize_arxiv_id("cond-mat/0211034") == "cond-mat/0211034"
    assert normalize_arxiv_id("cond-mat/0211034v3") == "cond-mat/0211034"
    assert normalize_arxiv_id("arXiv: cond-mat/0211034v3") == "cond-mat/0211034"
    assert normalize_arxiv_id(None) is None
    assert normalize_arxiv_id("  ") is None


def test_build_aliases_required_cases():
    assert build_aliases("abc123def4567890") == ["hash:abc123def4567890"]
    assert build_aliases(
        "abc123def4567890", doi="10.1000/test"
    ) == [
        "hash:abc123def4567890",
        "doi:10.1000/test",
    ]
    assert build_aliases(
        "abc123def4567890",
        arxiv_id="2401.12345",
        doi="10.1000/test",
    ) == [
        "hash:abc123def4567890",
        "arxiv:2401.12345",
        "doi:10.1000/test",
    ]
