from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PaperIdentity:
    doi: str | None = None
    arxiv_id: str | None = None
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PaperIdentity":
        return cls(
            doi=data.get("doi"),
            arxiv_id=data.get("arxiv_id"),
            aliases=list(data.get("aliases", [])),
        )
