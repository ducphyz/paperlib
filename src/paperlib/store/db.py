from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paperlib.handle import generate_handle_id
from paperlib.models.file import FileRecord
from paperlib.models.record import PaperRecord
from paperlib.store import migrations
from paperlib.store.json_store import (
    JsonStoreError,
    read_record,
    write_record_atomic,
)


class IdNotFound(LookupError):
    pass


def connect(db_path: str | Path) -> sqlite3.Connection:
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


def find_paper_id_by_handle(
    conn: sqlite3.Connection, handle_id: str
) -> str | None:
    row = conn.execute(
        "SELECT paper_id FROM papers WHERE handle_id = ?",
        (handle_id,),
    ).fetchone()
    return None if row is None else row["paper_id"]


def file_exists(conn: sqlite3.Connection, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM files WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()
    return row is not None


def find_paper_id_by_file_hash(
    conn: sqlite3.Connection, file_hash: str
) -> str | None:
    row = conn.execute(
        "SELECT paper_id FROM files WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()
    return None if row is None else row["paper_id"]


def list_handle_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT handle_id FROM papers WHERE handle_id IS NOT NULL"
    ).fetchall()
    return {row["handle_id"] for row in rows}


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
            handle_id,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            handle_id = excluded.handle_id,
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
            data.get("handle_id"),
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


def update_record_index(
    conn: sqlite3.Connection, record: PaperRecord | dict, record_path
) -> None:
    data = _record_dict(record)
    try:
        conn.execute("BEGIN")
        _upsert_paper_sql(conn, data, record_path)
        conn.execute(
            "DELETE FROM aliases WHERE paper_id = ?",
            (data["paper_id"],),
        )
        _insert_aliases_sql(conn, data["paper_id"], data["identity"]["aliases"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def delete_paper(conn: sqlite3.Connection, paper_id: str) -> None:
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM processing_runs WHERE paper_id = ?",
            (paper_id,),
        )
        conn.execute("DELETE FROM aliases WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM files WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
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


def resolve_id(conn: sqlite3.Connection, id_or_alias: str) -> str:
    attempted = []
    if id_or_alias.startswith("p_"):
        attempted.append("paper_id")
        row = conn.execute(
            "SELECT paper_id FROM papers WHERE paper_id = ?",
            (id_or_alias,),
        ).fetchone()
        if row is not None:
            return row["paper_id"]
        raise _id_not_found(id_or_alias, attempted)

    if ":" in id_or_alias:
        attempted.append("alias")
        paper_id = find_paper_id_by_alias(conn, id_or_alias)
        if paper_id is not None:
            return paper_id
        raise _id_not_found(id_or_alias, attempted)

    if re.fullmatch(r"[0-9a-fA-F]{16}", id_or_alias):
        attempted.append("bare hash")
        paper_id = find_paper_id_by_alias(conn, f"hash:{id_or_alias.lower()}")
        if paper_id is not None:
            return paper_id
        # A handle may itself look like a 16-char hash; only claim hash
        # resolution if the hash alias exists, then fall through to handle_id.

    attempted.append("handle_id")
    paper_id = find_paper_id_by_handle(conn, id_or_alias)
    if paper_id is not None:
        return paper_id

    raise _id_not_found(id_or_alias, attempted)


def get_record_path(conn: sqlite3.Connection, paper_id: str) -> str | None:
    row = conn.execute(
        "SELECT record_path FROM papers WHERE paper_id = ?",
        (paper_id,),
    ).fetchone()
    return None if row is None else row["record_path"]


def list_papers(
    conn: sqlite3.Connection,
    *,
    needs_review: bool = False,
    sort: str = "year",
) -> list[dict]:
    where = "WHERE review_status = 'needs_review'" if needs_review else ""
    if sort == "handle":
        order_by = "handle_id IS NULL ASC, handle_id ASC, paper_id ASC"
    elif sort == "year":
        order_by = "year IS NULL ASC, year DESC, paper_id ASC"
    else:
        raise ValueError(f"unsupported paper sort: {sort}")

    rows = conn.execute(
        f"""
        SELECT handle_id, paper_id, title, authors_json, year, review_status
        FROM papers
        {where}
        ORDER BY {order_by}
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_all_paper_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT paper_id, record_path, handle_id
        FROM papers
        ORDER BY paper_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_all_record_paths(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (paper_id, record_path) for every row, ordered by paper_id."""
    rows = conn.execute(
        """
        SELECT paper_id, record_path
        FROM papers
        ORDER BY paper_id
        """
    ).fetchall()
    return [(row["paper_id"], row["record_path"]) for row in rows]


def list_all_file_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT file_hash, paper_id, canonical_path, text_path
        FROM files
        ORDER BY paper_id, file_hash
        """
    ).fetchall()
    return [dict(row) for row in rows]


def rebuild_index_from_records(
    db_path: Path,
    records_dir: Path,
    *,
    dry_run: bool = False,
    backfill_handles: bool = True,
) -> dict:
    db_path = Path(db_path)
    records_dir = Path(records_dir)

    backup_path = None
    if not dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run and db_path.exists():
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

    handle_plan = (
        _plan_handle_backfill(records_to_load)
        if backfill_handles
        else _empty_handle_plan()
    )
    if dry_run:
        return {
            "records_loaded": len(records_to_load),
            "records_skipped": records_skipped,
            "json_errors": json_errors,
            "backup_path": None,
            "handles_added": handle_plan["handles_added"],
            "duplicate_handles_repaired": handle_plan[
                "duplicate_handles_repaired"
            ],
            "handle_updates": handle_plan["handle_updates"],
            "dry_run": True,
            "backfill_handles": backfill_handles,
        }

    for record_path, record, _reason in handle_plan["updates"]:
        write_record_atomic(record_path, record)

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
        "handles_added": handle_plan["handles_added"],
        "duplicate_handles_repaired": handle_plan[
            "duplicate_handles_repaired"
        ],
        "handle_updates": handle_plan["handle_updates"],
        "dry_run": False,
        "backfill_handles": backfill_handles,
    }


def _plan_handle_backfill(
    records_to_load: list[tuple[Path, PaperRecord]],
) -> dict:
    first_owner_by_handle: dict[str, Path] = {}
    for record_path, record in records_to_load:
        handle_id = _clean_handle_id(record.handle_id)
        if handle_id is None:
            continue
        if handle_id not in first_owner_by_handle:
            first_owner_by_handle[handle_id] = record_path

    used_handles = set(first_owner_by_handle)
    updates = []
    handles_added = 0
    duplicate_handles_repaired = 0

    for record_path, record in records_to_load:
        handle_id = _clean_handle_id(record.handle_id)
        if (
            handle_id is not None
            and first_owner_by_handle.get(handle_id) == record_path
        ):
            record.handle_id = handle_id
            continue

        record.handle_id = generate_handle_id(record, used_handles)
        used_handles.add(record.handle_id)
        reason = "missing" if handle_id is None else "duplicate"
        if reason == "missing":
            handles_added += 1
        else:
            duplicate_handles_repaired += 1
        updates.append((record_path, record, reason))

    return {
        "updates": updates,
        "handles_added": handles_added,
        "duplicate_handles_repaired": duplicate_handles_repaired,
        "handle_updates": len(updates),
    }


def _empty_handle_plan() -> dict:
    return {
        "updates": [],
        "handles_added": 0,
        "duplicate_handles_repaired": 0,
        "handle_updates": 0,
    }


def _clean_handle_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value.strip() else None


def _id_not_found(id_or_alias: str, attempted: list[str]) -> IdNotFound:
    namespaces = ", ".join(attempted)
    supported = "paper_id, doi:, arxiv:, hash:, bare hash, handle_id"
    return IdNotFound(
        f"Paper not found: {id_or_alias}. Tried: {namespaces}. "
        f"Supported namespaces: {supported}."
    )


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


def search_papers(conn: sqlite3.Connection, query: str, *, sort: str = "year") -> list[dict]:
    """Return rows where title OR authors_json contains query (case-insensitive).

    Literal % and _ in the query are treated as literals, not LIKE wildcards.
    """
    if sort == "handle":
        order_by = "handle_id IS NULL ASC, handle_id ASC, paper_id ASC"
    elif sort == "year":
        order_by = "year IS NULL ASC, year DESC, paper_id ASC"
    else:
        raise ValueError(f"unsupported paper sort: {sort}")

    # Escape LIKE metacharacters so user-supplied chars are literals.
    query_escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{query_escaped}%"

    rows = conn.execute(
        f"""
        SELECT handle_id, paper_id, title, authors_json, year, review_status
        FROM papers
        WHERE title LIKE ? ESCAPE '\\' OR authors_json LIKE ? ESCAPE '\\'
        ORDER BY {order_by}
        """,
        (pattern, pattern),
    ).fetchall()
    return [dict(row) for row in rows]


def list_resummary_candidates(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[dict]:
    """Return rows where summary_status IN ('failed', 'skipped').
    
    Each dict includes: paper_id, record_path, handle_id.
    Respects limit if given.
    """
    query = """
        SELECT paper_id, record_path, handle_id
        FROM papers
        WHERE summary_status IN ('failed', 'skipped')
    """
    params = []
    
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]
