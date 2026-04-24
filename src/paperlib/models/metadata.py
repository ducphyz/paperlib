from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MetadataField:
    value: Any = None
    source: str | None = None
    confidence: float | None = None
    locked: bool = False
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "locked": self.locked,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetadataField":
        return cls(
            value=data.get("value"),
            source=data.get("source"),
            confidence=data.get("confidence"),
            locked=bool(data.get("locked", False)),
            updated_at=data.get("updated_at"),
        )
