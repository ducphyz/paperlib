from pathlib import Path

from paperlib.models.record import PaperRecord
from paperlib.store import db
from paperlib.store.json_store import write_record_atomic


def _write_config(path: Path, root: Path) -> None:
    path.write_text(
        f"""
[library]
root = "{root}"

[paths]
inbox = "inbox"
papers = "papers"
records = "records"
text = "text"
db = "db/library.db"
logs = "logs"
failed = "failed"
duplicates = "duplicates"

[pipeline]
move_after_ingest = true
skip_existing = true
dry_run_default = false

[extraction]
engine = "pdfplumber"
min_char_count = 500
min_word_count = 100

[ai]
enabled = false
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_tokens = 1200
temperature = 0.2
""",
        encoding="utf-8",
    )


def _write_minimal_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    words = " ".join(f"word{i}" for i in range(140))
    text = (
        "arXiv:2401.12345 Published 12 March 2024 "
        f"DOI 10.1234/phase7 {words}"
    )

    try:
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError:
        _write_minimal_pdf_bytes(path, text)
        return

    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, text)
    pdf.save()


def _write_minimal_pdf_bytes(path: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        ),
    ]

    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(content)


def _relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*")}


def _insert_list_row(
    root: Path,
    *,
    paper_id: str,
    title,
    authors_json,
    year,
    handle_id: str | None = None,
    review_status: str = "needs_review",
) -> None:
    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        now = "2026-04-26T00:00:00Z"
        conn.execute(
            """
            INSERT INTO papers (
                paper_id, handle_id, title, authors_json, year,
                metadata_status, summary_status, duplicate_status,
                review_status, record_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'ok', 'pending', 'unique', ?, ?, ?, ?)
            """,
            (
                paper_id,
                handle_id,
                title,
                authors_json,
                year,
                review_status,
                f"records/{paper_id}.json",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO files (
                file_hash, paper_id, original_name, canonical_path, text_path,
                size_bytes, extraction_status, added_at
            )
            VALUES (?, ?, ?, ?, ?, 0, 'ok', ?)
            """,
            (
                f"{paper_id}_hash",
                paper_id,
                "paper.pdf",
                f"papers/{paper_id}.pdf",
                f"text/{paper_id}.txt",
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write_show_fixture(root: Path, config_path: Path) -> None:
    _write_config(config_path, root)
    records_dir = root / "records"
    records_dir.mkdir(parents=True)
    record = PaperRecord(paper_id="p_show", handle_id="show_2024")
    record.identity.doi = "10.1234/example"
    record.identity.arxiv_id = "2401.12345"
    record.identity.aliases = [
        "hash:abcdef1234567890",
        "arxiv:2401.12345",
        "doi:10.1234/example",
    ]
    write_record_atomic(records_dir / "p_show.json", record)

    conn = db.connect(root / "db" / "library.db")
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_show.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
    finally:
        conn.close()
