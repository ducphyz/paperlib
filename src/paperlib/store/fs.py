from __future__ import annotations

import hashlib
from pathlib import Path

from paperlib.config import AppConfig


def ensure_runtime_dirs(config: AppConfig) -> None:
    paths = config.paths
    for path in (
        paths.inbox,
        paths.papers,
        paths.records,
        paths.text,
        paths.db.parent,
        paths.logs,
        paths.failed,
        paths.duplicates,
    ):
        path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
