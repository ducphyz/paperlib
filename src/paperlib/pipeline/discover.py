from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from paperlib.store.fs import sha256_file


@dataclass(frozen=True)
class DiscoveredPDF:
    path: Path
    file_hash: str
    hash16: str
    hash8: str
    size_bytes: int
    modified_time: str


def discover_pdfs(inbox_path: Path) -> list[DiscoveredPDF]:
    discovered = []
    for path in sorted(inbox_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue

        file_hash = sha256_file(path)
        stat = path.stat()
        discovered.append(
            DiscoveredPDF(
                path=path,
                file_hash=file_hash,
                hash16=file_hash[:16],
                hash8=file_hash[:8],
                size_bytes=stat.st_size,
                modified_time=_format_modified_time(stat.st_mtime),
            )
        )
    return discovered


def _format_modified_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
