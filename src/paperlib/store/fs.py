from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
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


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def move_to_failed(path: Path, failed_dir: Path) -> Path:
    failed_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(failed_dir / path.name)
    return Path(shutil.move(str(path), str(destination)))


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination

    counter = 1
    candidate = destination.with_name(
        f"{destination.stem}_{counter}{destination.suffix}"
    )
    while candidate.exists():
        counter += 1
        candidate = destination.with_name(
            f"{destination.stem}_{counter}{destination.suffix}"
        )
    return candidate
