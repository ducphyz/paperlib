from pathlib import Path

from paperlib.store import db
from paperlib.store.json_store import write_record_atomic
from paperlib.store.migrations import SCHEMA_VERSION


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "db" / "library.db")
    db.init_db(conn)
    return conn


def _record(paper_id: str = "p_abc") -> dict:
    return {
        "paper_id": paper_id,
        "identity": {
            "doi": "10.1234/example",
            "arxiv_id": "2401.12345",
            "aliases": [],
        },
        "metadata": {
            "title": {"value": "A Paper"},
            "authors": {"value": ["Ada", "Grace"]},
            "year": {"value": 2024},
            "journal": {"value": "Journal"},
        },
        "status": {
            "metadata": "ok",
            "summary": "pending",
            "duplicate": "unique",
            "review": "needs_review",
        },
        "timestamps": {
            "created_at": "2026-04-25T00:00:00Z",
            "updated_at": "2026-04-25T00:00:00Z",
        },
    }


def _file(file_hash: str = "a" * 64) -> dict:
    return {
        "file_hash": file_hash,
        "original_filename": "paper.pdf",
        "canonical_path": "papers/2024/paper.pdf",
        "text_path": "text/aaaaaaaaaaaaaaaa.txt",
        "size_bytes": 123,
        "added_at": "2026-04-25T00:00:00Z",
        "extraction": {
            "page_count": 2,
            "char_count": 1000,
            "word_count": 200,
            "status": "ok",
            "quality": "good",
        },
    }


def test_init_db_creates_all_tables(tmp_path: Path):
    conn = _conn(tmp_path)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert {
        "papers",
        "aliases",
        "files",
        "processing_runs",
        "schema_migrations",
    }.issubset(tables)


def test_apply_migrations_is_idempotent(tmp_path: Path):
    conn = _conn(tmp_path)

    db.apply_migrations(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
        (SCHEMA_VERSION,),
    ).fetchone()[0]
    assert count == 1


def test_insert_paper_then_resolve_by_paper_id(tmp_path: Path):
    conn = _conn(tmp_path)

    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")

    assert db.resolve_id(conn, "p_abc") == "p_abc"


def test_upsert_paper_updates_existing_row(tmp_path: Path):
    conn = _conn(tmp_path)
    first = _record("p_abc")
    second = _record("p_abc")
    second["metadata"]["title"]["value"] = "Updated Title"
    second["metadata"]["year"]["value"] = 2025
    second["status"]["review"] = "reviewed"
    second["timestamps"]["updated_at"] = "2026-04-26T00:00:00Z"

    db.upsert_paper(conn, first, "records/p_abc.json")
    db.upsert_paper(conn, second, "records/p_abc.json")

    row = conn.execute(
        """
        SELECT title, year, review_status, updated_at
        FROM papers
        WHERE paper_id = 'p_abc'
        """
    ).fetchone()
    assert row["title"] == "Updated Title"
    assert row["year"] == 2025
    assert row["review_status"] == "reviewed"
    assert row["updated_at"] == "2026-04-26T00:00:00Z"


def test_insert_aliases_then_find_paper_id_by_alias(tmp_path: Path):
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")

    db.insert_aliases(conn, "p_abc", ["doi:10.1234/example"])

    assert db.find_paper_id_by_alias(conn, "doi:10.1234/example") == "p_abc"


def test_file_exists_false_before_insert_true_after(tmp_path: Path):
    conn = _conn(tmp_path)
    file_hash = "a" * 64
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")

    assert db.file_exists(conn, file_hash) is False

    db.insert_file(conn, "p_abc", _file(file_hash))

    assert db.file_exists(conn, file_hash) is True
    row = conn.execute(
        """
        SELECT paper_id, original_name, text_path, extraction_status
        FROM files
        WHERE file_hash = ?
        """,
        (file_hash,),
    ).fetchone()
    assert row["paper_id"] == "p_abc"
    assert row["original_name"] == "paper.pdf"
    assert row["text_path"] == "text/aaaaaaaaaaaaaaaa.txt"
    assert row["extraction_status"] == "ok"


def test_resolve_id_accepts_aliases_and_bare_hash(tmp_path: Path):
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")
    db.insert_aliases(
        conn,
        "p_abc",
        ["arxiv:2401.12345", "doi:10.1234/example", "hash:abcdef1234567890"],
    )

    assert db.resolve_id(conn, "p_abc") == "p_abc"
    assert db.resolve_id(conn, "arxiv:2401.12345") == "p_abc"
    assert db.resolve_id(conn, "doi:10.1234/example") == "p_abc"
    assert db.resolve_id(conn, "ABCDEF1234567890") == "p_abc"


def test_list_papers_returns_inserted_row(tmp_path: Path):
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")

    rows = db.list_papers(conn)

    assert rows == [
        {
            "paper_id": "p_abc",
            "title": "A Paper",
            "authors_json": '["Ada", "Grace"]',
            "year": 2024,
            "review_status": "needs_review",
        }
    ]


def test_get_status_counts(tmp_path: Path):
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")
    db.insert_file(conn, "p_abc", _file())

    counts = db.get_status_counts(conn)

    assert counts["papers"] == 1
    assert counts["files"] == 1
    assert counts["extraction_ok"] == 1
    assert counts["needs_review"] == 1
    assert counts["summary_pending"] == 1


def test_rebuild_index_from_records_loads_valid_records_and_skips_errors(
    tmp_path: Path,
):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    db_path = tmp_path / "db" / "library.db"
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_stale"), "records/p_stale.json")
    db.log_processing_run(
        conn, "a" * 64, "p_stale", "ingest", "ok", "stale"
    )
    conn.close()

    valid = _record("p_valid")
    valid["schema_version"] = SCHEMA_VERSION
    valid["identity"]["aliases"] = [
        "doi:10.1234/example",
        "hash:bbbbbbbbbbbbbbbb",
    ]
    valid["files"] = [_file("b" * 64)]
    write_record_atomic(records_dir / "p_valid.json", valid)
    (records_dir / "bad.json").write_text("{bad json", encoding="utf-8")
    write_record_atomic(
        records_dir / "wrong_schema.json",
        {"schema_version": SCHEMA_VERSION + 1, "paper_id": "p_wrong"},
    )

    result = db.rebuild_index_from_records(db_path, records_dir)

    assert result["records_loaded"] == 1
    assert result["records_skipped"] == 2
    assert result["json_errors"] == 2
    assert result["backup_path"] is not None
    assert Path(result["backup_path"]).exists()

    conn = db.connect(db_path)
    assert db.resolve_id(conn, "p_stale") is None
    assert db.resolve_id(conn, "p_valid") == "p_valid"
    assert db.file_exists(conn, "b" * 64) is True
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM processing_runs"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT record_path FROM papers WHERE paper_id = 'p_valid'"
    ).fetchone()[0] == "records/p_valid.json"
    conn.close()
