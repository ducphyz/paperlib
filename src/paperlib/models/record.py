from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from paperlib.models import status as status_values
from paperlib.models.file import FileRecord
from paperlib.models.identity import PaperIdentity
from paperlib.models.metadata import MetadataField


def _default_metadata() -> dict[str, MetadataField]:
    return {
        "title": MetadataField(),
        "authors": MetadataField(),
        "year": MetadataField(),
        "journal": MetadataField(),
    }


def _default_summary() -> dict:
    return {
        "status": status_values.SUMMARY_PENDING,
        "source_file_hash": None,
        "model": None,
        "prompt_version": None,
        "generated_at": None,
        "locked": False,
        "one_sentence": None,
        "short": None,
        "technical": None,
        "key_contributions": [],
        "methods": [],
        "limitations": [],
        "physics": {
            "field": None,
            "materials": [],
            "devices": [],
            "measurements": [],
            "main_theory": [],
        },
        "tags": [],
    }


def _default_status() -> dict:
    return {
        "metadata": status_values.METADATA_PENDING,
        "summary": status_values.SUMMARY_PENDING,
        "duplicate": status_values.DUPLICATE_UNIQUE,
        "review": status_values.REVIEW_NEEDS_REVIEW,
    }


def _default_review() -> dict:
    return {"notes": "", "locked": False}


def _default_timestamps() -> dict:
    return {"created_at": None, "updated_at": None}


@dataclass
class PaperRecord:
    schema_version: int = 1
    paper_id: str = ""
    handle_id: str | None = None
    identity: PaperIdentity = field(default_factory=PaperIdentity)
    files: list[FileRecord] = field(default_factory=list)
    metadata: dict[str, MetadataField] = field(default_factory=_default_metadata)
    summary: dict = field(default_factory=_default_summary)
    status: dict = field(default_factory=_default_status)
    review: dict = field(default_factory=_default_review)
    timestamps: dict = field(default_factory=_default_timestamps)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "handle_id": self.handle_id,
            "identity": self.identity.to_dict(),
            "files": [file_record.to_dict() for file_record in self.files],
            "metadata": {
                name: field_value.to_dict()
                for name, field_value in self.metadata.items()
            },
            "summary": deepcopy(self.summary),
            "status": dict(self.status),
            "review": dict(self.review),
            "timestamps": dict(self.timestamps),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PaperRecord":
        metadata_data = data.get("metadata", {})
        metadata = _default_metadata()
        metadata.update(
            {
                name: MetadataField.from_dict(field_data)
                for name, field_data in metadata_data.items()
                if name in metadata
            }
        )

        summary = _merge_dict(_default_summary(), data.get("summary", {}))
        if isinstance(summary.get("physics"), dict):
            summary["physics"] = _merge_dict(
                _default_summary()["physics"], summary["physics"]
            )

        status = _merge_dict(_default_status(), data.get("status", {}))
        review = _merge_dict(_default_review(), data.get("review", {}))
        timestamps = _merge_dict(
            _default_timestamps(), data.get("timestamps", {})
        )

        return cls(
            schema_version=data.get("schema_version", 1),
            paper_id=data.get("paper_id", ""),
            handle_id=data.get("handle_id"),
            identity=PaperIdentity.from_dict(data.get("identity", {})),
            files=[
                FileRecord.from_dict(file_data)
                for file_data in data.get("files", [])
            ],
            metadata=metadata,
            summary=summary,
            status=status,
            review=review,
            timestamps=timestamps,
        )


def _merge_dict(defaults: dict, data: dict) -> dict:
    merged = deepcopy(defaults)
    for key, value in data.items():
        if key in merged:
            merged[key] = value
    return merged
