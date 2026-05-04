from __future__ import annotations

from paperlib.models.record import PaperRecord


# Escapes applied in order to avoid double-escaping.
_BIBTEX_ESCAPES: list[tuple[str, str]] = [
    ("\\", "\\textbackslash{}"),
    ("&", "\\&"),
    ("%", "\\%"),
    ("$", "\\$"),
    ("#", "\\#"),
    ("_", "\\_"),
]


def _escape(value: str) -> str:
    """Apply BibTeX character escaping to a plain string value."""
    for char, replacement in _BIBTEX_ESCAPES:
        value = value.replace(char, replacement)
    return value


def record_to_bibtex(record: PaperRecord) -> str:
    """Convert a single PaperRecord to a BibTeX entry string."""
    doi = record.identity.doi
    arxiv_id = record.identity.arxiv_id

    # Entry type
    entry_type = "article" if doi else "misc"

    # Cite key: handle_id preferred, fall back to paper_id
    cite_key = record.handle_id if record.handle_id else record.paper_id

    fields: list[tuple[str, str]] = []

    # title
    title_field = record.metadata.get("title")
    if title_field is not None and title_field.value is not None:
        fields.append(("title", _escape(str(title_field.value))))

    # author
    authors_field = record.metadata.get("authors")
    if authors_field is not None and authors_field.value is not None:
        author_val = authors_field.value
        if isinstance(author_val, list):
            author_str = " and ".join(str(a) for a in author_val)
        else:
            author_str = str(author_val)
        fields.append(("author", _escape(author_str)))

    # year
    year_field = record.metadata.get("year")
    if year_field is not None and year_field.value is not None:
        fields.append(("year", _escape(str(year_field.value))))

    # journal — @article only
    if entry_type == "article":
        journal_field = record.metadata.get("journal")
        if journal_field is not None and journal_field.value is not None:
            fields.append(("journal", _escape(str(journal_field.value))))

    # doi
    if doi:
        fields.append(("doi", _escape(doi)))

    # eprint / archivePrefix — arXiv @misc only
    if arxiv_id and not doi:
        fields.append(("eprint", _escape(arxiv_id)))
        fields.append(("archivePrefix", "arXiv"))

    # note — neither DOI nor arXiv
    if not doi and not arxiv_id:
        fields.append(("note", _escape(f"paperlib:{record.paper_id}")))

    field_lines = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    if field_lines:
        field_lines += "\n"
    return f"@{entry_type}{{{cite_key},\n{field_lines}}}"


def records_to_bibtex(records: list[PaperRecord]) -> str:
    """Convert a list of PaperRecords to a multi-entry BibTeX string."""
    return "\n\n".join(record_to_bibtex(r) for r in records)
