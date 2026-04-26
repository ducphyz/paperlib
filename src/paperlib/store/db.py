from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperlib.models.file import FileRecord
from paperlib.models.record import PaperRecord
from paperlib.store import migrations
from paperlib.store.json_store import JsonStoreError, read_record


def connect(db_path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)


def apply_migrations(conn: sqlite3.Connection) -> None:
    migrations.apply_migrations(conn)


def find_paper_id_by_alias(
    conn: sqlite3.Connection, alias: str
) -> str | None:
    row = conn.execute(
        "SELECT paper_id FROM aliases WHERE alias = ?",
        (alias,),
    ).fetchone()
    return None if row is None else row["paper_id"]


def file_exists(conn: sqlite3.Connection, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM files WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()
    return row is not None


def upsert_paper(
    conn: sqlite3.Connection, record: PaperRecord | dict, record_path
) -> None:
    _upsert_paper_sql(conn, record, record_path)
    conn.commit()


def _upsert_paper_sql(
    conn: sqlite3.Connection, record: PaperRecord | dict, record_path
) -> None:
    data = _record_dict(record)
    metadata = data.get("metadata", {})
    identity = data.get("identity", {})
    status = data.get("status", {})
    timestamps = data.get("timestamps", {})
    authors = _metadata_value(metadata, "authors")
    now = _utc_now()

    conn.execute(
        """
        INSERT INTO papers (
            paper_id,
            title,
            authors_json,
            year,
            journal,
            doi,
            arxiv_id,
            metadata_status,
            summary_status,
            duplicate_status,
            review_status,
            record_path,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            title = excluded.title,
            authors_json = excluded.authors_json,
            year = excluded.year,
            journal = excluded.journal,
            doi = excluded.doi,
            arxiv_id = excluded.arxiv_id,
            metadata_status = excluded.metadata_status,
            summary_status = excluded.summary_status,
            duplicate_status = excluded.duplicate_status,
            review_status = excluded.review_status,
            record_path = excluded.record_path,
            updated_at = excluded.updated_at
        """,
        (
            data["paper_id"],
            _metadata_value(metadata, "title"),
            None if authors is None else json.dumps(authors, ensure_ascii=False),
            _metadata_value(metadata, "year"),
            _metadata_value(metadata, "journal"),
            identity.get("doi"),
            identity.get("arxiv_id"),
            status.get("metadata", "pending"),
            status.get("summary", "pending"),
            status.get("duplicate", "unique"),
            status.get("review", "needs_review"),
            str(record_path),
            timestamps.get("created_at") or now,
            timestamps.get("updated_at") or now,
        ),
    )


def insert_aliases(
    conn: sqlite3.Connection, paper_id: str, aliases: list[str]
) -> None:
    _insert_aliases_sql(conn, paper_id, aliases)
    conn.commit()


def _insert_aliases_sql(
    conn: sqlite3.Connection, paper_id: str, aliases: list[str]
) -> None:
    created_at = _utc_now()
    conn.executemany(
        """
        INSERT OR IGNORE INTO aliases (
            alias, paper_id, alias_type, created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (alias, paper_id, _alias_type(alias), created_at)
            for alias in aliases
        ],
    )


def insert_file(
    conn: sqlite3.Connection, paper_id: str, file_record: FileRecord | dict
) -> None:
    _insert_file_sql(conn, paper_id, file_record)
    conn.commit()


def _insert_file_sql(
    conn: sqlite3.Connection, paper_id: str, file_record: FileRecord | dict
) -> None:
    data = _file_dict(file_record)
    extraction = data.get("extraction", {})
    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_hash,
            paper_id,
            original_name,
            canonical_path,
            text_path,
            size_bytes,
            page_count,
            char_count,
            word_count,
            extraction_status,
            extraction_quality,
            added_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["file_hash"],
            paper_id,
            data.get("original_filename"),
            data.get("canonical_path"),
            data.get("text_path"),
            data.get("size_bytes"),
            extraction.get("page_count"),
            extraction.get("char_count"),
            extraction.get("word_count"),
            extraction.get("status", "pending"),
            extraction.get("quality"),
            data["added_at"],
        ),
    )


def log_processing_run(
    conn: sqlite3.Connection,
    file_hash: str | None,
    paper_id: str | None,
    stage: str,
    status: str,
    message: str | None,
) -> None:
    _log_processing_run_sql(conn, file_hash, paper_id, stage, status, message)
    conn.commit()


def record_ingest_success(
    conn: sqlite3.Connection,
    record: PaperRecord | dict,
    file_record: FileRecord | dict,
    record_path: str,
) -> None:
    data = _record_dict(record)
    file_data = _file_dict(file_record)
    try:
        conn.execute("BEGIN")
        _upsert_paper_sql(conn, data, record_path)
        _insert_aliases_sql(conn, data["paper_id"], data["identity"]["aliases"])
        _insert_file_sql(conn, data["paper_id"], file_data)
        _log_processing_run_sql(
            conn,
            file_data["file_hash"],
            data["paper_id"],
            "ingest",
            "ok",
            None,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _log_processing_run_sql(
    conn: sqlite3.Connection,
    file_hash: str | None,
    paper_id: str | None,
    stage: str,
    status: str,
    message: str | None,
) -> None:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO processing_runs (
            file_hash, paper_id, stage, status, message, started_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (file_hash, paper_id, stage, status, message, now, now),
    )


def get_status_counts(conn: sqlite3.Connection) -> dict:
    return {
        "papers": _count(conn, "papers"),
        "files": _count(conn, "files"),
        "extraction_ok": _count_where(
            conn, "files", "extraction_status = 'ok'"
        ),
        "extraction_partial": _count_where(
            conn, "files", "extraction_status = 'partial'"
        ),
        "extraction_failed": _count_where(
            conn, "files", "extraction_status = 'failed'"
        ),
        "needs_review": _count_where(
            conn, "papers", "review_status = 'needs_review'"
        ),
        "summary_pending": _count_where(
            conn, "papers", "summary_status = 'pending'"
        ),
        "summary_failed": _count_where(
            conn, "papers", "summary_status = 'failed'"
        ),
    }


def resolve_id(conn: sqlite3.Connection, id_or_alias: str) -> str | None:
    if id_or_alias.startswith("p_"):
        row = conn.execute(
            "SELECT paper_id FROM papers WHERE paper_id = ?",
            (id_or_alias,),
        ).fetchone()
        return None if row is None else row["paper_id"]

    if ":" in id_or_alias:
        return find_paper_id_by_alias(conn, id_or_alias)

    if re.fullmatch(r"[0-9a-fA-F]{16}", id_or_alias):
        return find_paper_id_by_alias(conn, f"hash:{id_or_alias.lower()}")

    return None


def get_record_path(conn: sqlite3.Connection, paper_id: str) -> str | None:
    row = conn.execute(
        "SELECT record_path FROM papers WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    return None if row is None else row["record_path"]


def list_papers(
    conn: sqlite3.Connection, *, needs_review: bool = False
) -> list[dict]:
    where = "WHERE review_status = 'needs_review'" if needs_review else ""
    rows = conn.execute(
        f"""
        SELECT paper_id, title, authors_json, year, review_status
        FROM papers
        {where}
        ORDER BY year IS NULL ASC, year DESC, paper_id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def rebuild_index_from_records(db_path: Path, records_dir: Path) -> dict:
    db_path = Path(db_path)
    records_dir = Path(records_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if db_path.exists():
        backup_path = db_path.with_name(
            f"{db_path.stem}.backup-{_backup_timestamp()}{db_path.suffix}"
        )
        shutil.copy2(db_path, backup_path)

    records_to_load = []
    records_skipped = 0
    json_errors = 0
    for record_path in sorted(records_dir.glob("*.json")):
        try:
            records_to_load.append((record_path, read_record(record_path)))
        except (JsonStoreError, json.JSONDecodeError, OSError):
            records_skipped += 1
            json_errors += 1

    conn = connect(db_path)
    init_db(conn)
    try:
        conn.execute("BEGIN")
        _clear_index_tables(conn)
        for record_path, record in records_to_load:
            _index_record(conn, record, f"records/{record_path.name}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "records_loaded": len(records_to_load),
        "records_skipped": records_skipped,
        "json_errors": json_errors,
        "backup_path": None if backup_path is None else str(backup_path),
    }


def _record_dict(record: PaperRecord | dict) -> dict:
    return record.to_dict() if isinstance(record, PaperRecord) else record


def _file_dict(file_record: FileRecord | dict) -> dict:
    return (
        file_record.to_dict()
        if isinstance(file_record, FileRecord)
        else file_record
    )


def _metadata_value(metadata: dict[str, Any], field_name: str) -> Any:
    field = metadata.get(field_name, {})
    if hasattr(field, "to_dict"):
        field = field.to_dict()
    return field.get("value") if isinstance(field, dict) else None


def _alias_type(alias: str) -> str:
    return alias.split(":", 1)[0] if ":" in alias else "unknown"


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _count_where(
    conn: sqlite3.Connection, table: str, predicate: str
) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {predicate}"
    ).fetchone()[0]


def _clear_index_tables(conn: sqlite3.Connection) -> None:
    for table in ("processing_runs", "aliases", "files", "papers"):
        conn.execute(f"DELETE FROM {table}")


def _index_record(
    conn: sqlite3.Connection, record: PaperRecord, record_path: str
) -> None:
    _upsert_paper_sql(conn, record, record_path)
    _insert_aliases_sql(conn, record.paper_id, record.identity.aliases)
    for file_record in record.files:
        _insert_file_sql(conn, record.paper_id, file_record)


def _backup_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
