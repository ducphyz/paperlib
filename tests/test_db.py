from pathlib import Path
import re

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


def test_get_record_path_returns_path_for_existing_paper(tmp_path: Path):
    conn = _conn(tmp_path)
    db.upsert_paper(conn, _record("p_abc"), "records/p_abc.json")

    assert db.get_record_path(conn, "p_abc") == "records/p_abc.json"
    assert db.get_record_path(conn, "p_missing") is None


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


def test_list_papers_sorts_by_year_desc_null_last_then_paper_id(tmp_path: Path):
    conn = _conn(tmp_path)
    older = _record("p_older")
    older["metadata"]["year"]["value"] = 2023
    newer_b = _record("p_newer_b")
    newer_b["metadata"]["year"]["value"] = 2025
    newer_a = _record("p_newer_a")
    newer_a["metadata"]["year"]["value"] = 2025
    missing = _record("p_missing_year")
    missing["metadata"]["year"]["value"] = None

    for record in (older, newer_b, missing, newer_a):
        db.upsert_paper(conn, record, f"records/{record['paper_id']}.json")

    rows = db.list_papers(conn)

    assert [row["paper_id"] for row in rows] == [
        "p_newer_a",
        "p_newer_b",
        "p_older",
        "p_missing_year",
    ]


def test_list_papers_needs_review_filters_review_status(tmp_path: Path):
    conn = _conn(tmp_path)
    needs_review = _record("p_needs")
    reviewed = _record("p_reviewed")
    reviewed["status"]["review"] = "reviewed"
    db.upsert_paper(conn, needs_review, "records/p_needs.json")
    db.upsert_paper(conn, reviewed, "records/p_reviewed.json")

    rows = db.list_papers(conn, needs_review=True)

    assert [row["paper_id"] for row in rows] == ["p_needs"]


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


def test_get_status_counts_returns_all_required_values(tmp_path: Path):
    conn = _conn(tmp_path)
    first = _record("p_first")
    first["status"]["summary"] = "pending"
    first["status"]["review"] = "needs_review"
    second = _record("p_second")
    second["status"]["summary"] = "failed"
    second["status"]["review"] = "reviewed"

    db.upsert_paper(conn, first, "records/p_first.json")
    db.upsert_paper(conn, second, "records/p_second.json")
    db.insert_file(conn, "p_first", _file("a" * 64))
    partial = _file("b" * 64)
    partial["extraction"]["status"] = "partial"
    db.insert_file(conn, "p_second", partial)
    failed = _file("c" * 64)
    failed["extraction"]["status"] = "failed"
    db.insert_file(conn, "p_second", failed)

    counts = db.get_status_counts(conn)

    assert counts == {
        "papers": 2,
        "files": 3,
        "extraction_ok": 1,
        "extraction_partial": 1,
        "extraction_failed": 1,
        "needs_review": 1,
        "summary_pending": 1,
        "summary_failed": 1,
    }


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
    assert re.search(
        r"library\.backup-\d{8}-\d{6}\.db$",
        result["backup_path"],
    )

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


def test_rebuild_index_counts_match_multiple_valid_records(tmp_path: Path):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    db_path = tmp_path / "db" / "library.db"

    first = _record("p_first")
    first["schema_version"] = SCHEMA_VERSION
    first["identity"]["aliases"] = [
        "hash:1111111111111111",
        "arxiv:2401.11111",
    ]
    first["files"] = [_file("1" * 64)]
    second = _record("p_second")
    second["schema_version"] = SCHEMA_VERSION
    second["identity"]["aliases"] = ["hash:2222222222222222"]
    second["files"] = [_file("2" * 64), _file("3" * 64)]

    write_record_atomic(records_dir / "p_first.json", first)
    write_record_atomic(records_dir / "p_second.json", second)

    result = db.rebuild_index_from_records(db_path, records_dir)

    assert result == {
        "records_loaded": 2,
        "records_skipped": 0,
        "json_errors": 0,
        "backup_path": None,
    }

    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 3
        assert (
            conn.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
            == 0
        )
    finally:
        conn.close()
