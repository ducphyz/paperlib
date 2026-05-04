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
        paths.deleted,
        paths.duplicates,
    ):
        path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_LATIN_MAP = str.maketrans(
    {
        # Scandinavian
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "Ae",
        "å": "a",
        "Å": "A",
        # Polish / Baltic
        "ł": "l",
        "Ł": "L",
        # Icelandic / Old English
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        # German sharp s
        "ß": "ss",
        # Dotless i / dotted capital I
        "ı": "i",
        "İ": "I",
    }
)
_SURNAME_PARTICLES = {
    "da",
    "de",
    "den",
    "der",
    "di",
    "du",
    "la",
    "le",
    "ten",
    "ter",
    "van",
    "von",
}
_NAME_SUFFIXES = {"jr", "junior", "sr", "senior", "ii", "iii", "iv"}


def ascii_fold(s: str) -> str:
    s = s.translate(_LATIN_MAP)
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


def filename_author_component(first_author: str | None) -> str | None:
    if not first_author or not first_author.strip():
        return None

    tokens = [
        token.strip()
        for token in re.split(r"\s+", first_author.strip())
        if token.strip()
    ]
    if not tokens:
        return None

    while len(tokens) > 1 and _name_token_key(tokens[-1]) in _NAME_SUFFIXES:
        tokens.pop()
    if not tokens:
        return None

    surname_tokens = [tokens[-1]]
    index = len(tokens) - 2
    while index >= 0 and _name_token_key(tokens[index]) in _SURNAME_PARTICLES:
        surname_tokens.insert(0, tokens[index])
        index -= 1

    component = sanitize_component(" ".join(surname_tokens))
    return component or None


def canonical_pdf_relative_path(
    *,
    year: int | None,
    first_author: str | None,
    file_hash: str,
) -> str:
    year_component = str(year) if year is not None else "unknown_year"
    author_component = filename_author_component(first_author) or ""
    if not author_component:
        author_component = "unknown_author"

    hash8 = file_hash[:8]
    filename = f"{author_component}_{year_component}_{hash8}.pdf"
    directory = f"papers/{year_component}"
    return f"{directory}/{filename}"


def _name_token_key(token: str) -> str:
    return sanitize_component(token).replace("_", "")


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


def _move_to_dir(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(dest_dir / src.name)
    move_file(src, destination)
    return destination


def move_to_failed(src: Path, failed_dir: Path) -> Path:
    return _move_to_dir(src, failed_dir)


def move_to_deleted(src: Path, deleted_dir: Path) -> Path:
    return _move_to_dir(src, deleted_dir)


def move_to_duplicates(src: Path, duplicates_dir: Path) -> Path:
    return _move_to_dir(src, duplicates_dir)


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
