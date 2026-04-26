from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
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


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode(
        "ascii", "ignore"
    ).decode("ascii")


def sanitize_component(s: str, max_len: int = 40) -> str:
    if not s:
        return ""

    sanitized = ascii_fold(s).lower()
    sanitized = re.sub(r"[\s/\\,;:]+", "_", sanitized)
    sanitized = re.sub(r"[^a-z0-9_-]", "", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_-")
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip("_-")
    return sanitized


def canonical_pdf_relative_path(
    *,
    year: int | None,
    first_author: str | None,
    file_hash: str,
) -> str:
    year_component = str(year) if year is not None else "unknown_year"
    author_component = (
        sanitize_component(first_author) if first_author is not None else ""
    )
    if not author_component:
        author_component = "unknown_author"

    hash8 = file_hash[:8]
    filename = f"{year_component}_{author_component}_{hash8}.pdf"
    directory = f"papers/{year_component}"
    return f"{directory}/{filename}"


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


def move_file(src: Path, dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(src), str(dst))


def move_to_failed(src: Path, failed_dir: Path) -> Path:
    failed_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(failed_dir / src.name)
    move_file(src, destination)
    return destination


def move_to_duplicates(src: Path, duplicates_dir: Path) -> Path:
    duplicates_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(duplicates_dir / src.name)
    move_file(src, destination)
    return destination


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
