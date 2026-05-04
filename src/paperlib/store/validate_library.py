from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from paperlib.config import AppConfig
from paperlib.store.db import list_all_paper_rows
from paperlib.store.json_store import read_record
from paperlib.utils import resolve_library_path


@dataclass
class Finding:
    severity: str  # "error" | "warning" | "info"
    category: str
    detail: str


def validate_library(config: AppConfig) -> list[Finding]:
    findings = []
    
    # Check if database file exists
    if not Path(config.paths.db).exists():
        return [Finding("error", "MISSING_DB", f"SQLite database not found: {config.paths.db}")]
    
    # Connect to database in read-only mode
    conn = None
    try:
        conn = sqlite3.connect(f"file:{config.paths.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        
        paper_rows = list_all_paper_rows(conn)
    except Exception:
        if conn:
            conn.close()
        return [Finding("error", "MISSING_DB", f"SQLite database not found: {config.paths.db}")]
    
    db_paper_ids = {row['paper_id'] for row in paper_rows}
    
    # Dictionary mapping paper_id to record paths for database entries
    db_record_paths = {row['paper_id']: row['record_path'] for row in paper_rows}
    
    # Check JSON records
    json_paper_ids = set()
    json_canonical_paths: set[Path] = set()  # all canonical paths across all JSON records

    if config.paths.records.exists():
        for json_path in config.paths.records.glob("*.json"):
            try:
                record = read_record(json_path)
                record_dict = record if isinstance(record, dict) else record.to_dict()

                paper_id = record_dict.get('paper_id')
                if paper_id:
                    json_paper_ids.add(paper_id)

                for file_entry in record_dict.get('files', []):
                    cp = file_entry.get('canonical_path')
                    if cp:
                        canonical_path = resolve_library_path(
                            config.library.root, cp
                        )
                        json_canonical_paths.add(canonical_path.resolve())
                        if not canonical_path.exists():
                            findings.append(
                                Finding(
                                    "error",
                                    "MISSING_PDF",
                                    f"canonical_path {cp} in JSON not found on disk",
                                )
                            )

                    text_path = file_entry.get('text_path')
                    if text_path:
                        resolved_text_path = resolve_library_path(
                            config.library.root, text_path
                        )
                        if not resolved_text_path.exists():
                            findings.append(
                                Finding(
                                    "error",
                                    "MISSING_TEXT",
                                    f"text_path {text_path} in JSON not found on disk",
                                )
                            )
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                findings.append(Finding("error", "BAD_JSON", f"Invalid JSON in file: {json_path}"))
            except Exception:
                findings.append(Finding("error", "BAD_JSON", f"Could not read JSON file: {json_path}"))
    
    # Check for JSON records that don't exist in database (JSON_NOT_IN_DB)
    for json_paper_id in json_paper_ids:
        if json_paper_id not in db_paper_ids:
            findings.append(Finding("error", "JSON_NOT_IN_DB", f"JSON record with paper_id {json_paper_id} has no matching database entry"))
    
    # Check for database records whose record_path does not resolve to a file (DB_NOT_IN_JSON)
    for paper_row in paper_rows:
        record_path = paper_row['record_path']
        resolved_path = resolve_library_path(config.library.root, record_path)
        
        if not resolved_path.exists():
            findings.append(Finding("error", "DB_NOT_IN_JSON", f"Database record_path {record_path} does not resolve to an existing file"))

    # Find PDFs in papers/ not referenced by any JSON record (JSON is canonical)
    papers_dir = config.paths.papers
    if papers_dir.exists():
        for paper_file in papers_dir.rglob('*'):
            if paper_file.is_file() and paper_file.suffix.lower() == '.pdf':
                if paper_file.resolve() not in json_canonical_paths:
                    findings.append(Finding("warning", "ORPHAN_PDF", f"PDF file {paper_file} not referenced by any JSON record"))
    
    if conn:
        conn.close()
    return findings
