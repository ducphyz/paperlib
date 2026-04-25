from __future__ import annotations

import re
from dataclasses import dataclass, field


_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:)",
    re.IGNORECASE,
)
_ARXIV_PREFIX_RE = re.compile(r"^arxiv:\s*", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)


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


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = _DOI_PREFIX_RE.sub("", value.strip()).lower()
    normalized = normalized.rstrip(".,;)")
    return normalized or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = _ARXIV_PREFIX_RE.sub("", value.strip()).strip()
    normalized = _ARXIV_VERSION_RE.sub("", normalized)
    return normalized or None


def build_aliases(
    hash16: str, doi: str | None = None, arxiv_id: str | None = None
) -> list[str]:
    aliases = [f"hash:{hash16}"]
    if arxiv_id is not None:
        aliases.append(f"arxiv:{arxiv_id}")
    if doi is not None:
        aliases.append(f"doi:{doi}")

    deduplicated = []
    for alias in aliases:
        if alias not in deduplicated:
            deduplicated.append(alias)
    return deduplicated
