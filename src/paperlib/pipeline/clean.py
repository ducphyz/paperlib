from __future__ import annotations

import re


_LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}


def clean_text(raw: str) -> str:
    text = "" if raw is None else str(raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        char
        for char in text
        if ord(char) >= 32 or char in {"\n", "\t"}
    )
    for ligature, replacement in _LIGATURES.items():
        text = text.replace(ligature, replacement)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n"))
