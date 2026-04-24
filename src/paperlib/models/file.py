from __future__ import annotations

from dataclasses import dataclass, field

from paperlib.models import status as status_values


@dataclass
class ExtractionInfo:
    status: str = status_values.EXTRACTION_PENDING
    engine: str | None = None
    engine_version: str | None = None
    page_count: int | None = None
    char_count: int | None = None
    word_count: int | None = None
    quality: str = status_values.QUALITY_UNKNOWN
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "page_count": self.page_count,
            "char_count": self.char_count,
            "word_count": self.word_count,
            "quality": self.quality,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionInfo":
        return cls(
            status=data.get("status", status_values.EXTRACTION_PENDING),
            engine=data.get("engine"),
            engine_version=data.get("engine_version"),
            page_count=data.get("page_count"),
            char_count=data.get("char_count"),
            word_count=data.get("word_count"),
            quality=data.get("quality", status_values.QUALITY_UNKNOWN),
            warnings=list(data.get("warnings", [])),
        )


@dataclass
class FileRecord:
    file_hash: str
    original_filename: str
    canonical_path: str
    text_path: str
    size_bytes: int
    added_at: str
    extraction: ExtractionInfo = field(default_factory=ExtractionInfo)

    def to_dict(self) -> dict:
        return {
            "file_hash": self.file_hash,
            "original_filename": self.original_filename,
            "canonical_path": self.canonical_path,
            "text_path": self.text_path,
            "size_bytes": self.size_bytes,
            "added_at": self.added_at,
            "extraction": self.extraction.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileRecord":
        return cls(
            file_hash=data["file_hash"],
            original_filename=data["original_filename"],
            canonical_path=data["canonical_path"],
            text_path=data["text_path"],
            size_bytes=data["size_bytes"],
            added_at=data["added_at"],
            extraction=ExtractionInfo.from_dict(data.get("extraction", {})),
        )
