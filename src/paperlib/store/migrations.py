from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 2


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            paper_id         TEXT PRIMARY KEY,
            handle_id        TEXT,
            title            TEXT,
            authors_json     TEXT,
            year             INTEGER,
            journal          TEXT,
            doi              TEXT,
            arxiv_id         TEXT,
            metadata_status  TEXT NOT NULL DEFAULT 'pending',
            summary_status   TEXT NOT NULL DEFAULT 'pending',
            duplicate_status TEXT NOT NULL DEFAULT 'unique',
            review_status    TEXT NOT NULL DEFAULT 'needs_review',
            record_path      TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS aliases (
            alias       TEXT PRIMARY KEY,
            paper_id    TEXT NOT NULL,
            alias_type  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
        );

        CREATE TABLE IF NOT EXISTS files (
            file_hash          TEXT PRIMARY KEY,
            paper_id           TEXT NOT NULL,
            original_name      TEXT,
            canonical_path     TEXT,
            text_path          TEXT,
            size_bytes         INTEGER,
            page_count         INTEGER,
            char_count         INTEGER,
            word_count         INTEGER,
            extraction_status  TEXT NOT NULL DEFAULT 'pending',
            extraction_quality TEXT,
            added_at           TEXT NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
        );

        CREATE TABLE IF NOT EXISTS processing_runs (
            run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash    TEXT,
            paper_id     TEXT,
            stage        TEXT NOT NULL,
            status       TEXT NOT NULL,
            message      TEXT,
            started_at   TEXT NOT NULL,
            finished_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_papers_doi
            ON papers(doi);
        CREATE INDEX IF NOT EXISTS idx_papers_arxiv
            ON papers(arxiv_id);
        CREATE INDEX IF NOT EXISTS idx_papers_year
            ON papers(year);
        CREATE INDEX IF NOT EXISTS idx_papers_review
            ON papers(review_status);
        CREATE INDEX IF NOT EXISTS idx_files_paper
            ON files(paper_id);
        CREATE INDEX IF NOT EXISTS idx_aliases_paper
            ON aliases(paper_id);
        """
    )
    _migrate_to_v2(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version, applied_at)
        VALUES (?, datetime('now'))
        """,
        (SCHEMA_VERSION,),
    )
    conn.commit()


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "papers", "handle_id"):
        conn.execute("ALTER TABLE papers ADD COLUMN handle_id TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_handle_id
            ON papers(handle_id)
            WHERE handle_id IS NOT NULL
        """
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)
