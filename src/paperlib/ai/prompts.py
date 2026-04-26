from __future__ import annotations


SUMMARY_PROMPT_VERSION = "v1"


def build_summary_prompt(
    *,
    cleaned_text: str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    max_chars: int = 40000,
) -> str:
    truncated_text = cleaned_text[:max_chars]
    text_block = (
        truncated_text
        if truncated_text.strip()
        else "[The extracted text is empty.]"
    )
    hints = []
    if doi is not None:
        hints.append(f"DOI hint: {doi}")
    if arxiv_id is not None:
        hints.append(f"arXiv ID hint: {arxiv_id}")
    hints_block = "\n".join(hints) if hints else "No identifier hints provided."

    return f"""You extract paper metadata and write structured summaries.

Return a single JSON object only.
Do not use markdown fences. No markdown fences.
Do not write prose before or after the JSON.
Use null for unknown fields.
Do not fabricate.
Include all required keys even if values are null or empty arrays.
Authors must be a JSON array of strings, first author first.
one_sentence must be <= 30 words.
short must be <= 80 words.
technical must be <= 300 words.

The JSON object must contain exactly these top-level keys:
- title: string or null
- authors: array of strings or null
- journal: string or null
- one_sentence: string or null
- short: string or null
- technical: string or null
- key_contributions: array of strings
- methods: array of strings
- limitations: array of strings
- physics: object with exactly these keys:
  - field: string or null
  - materials: array of strings
  - devices: array of strings
  - measurements: array of strings
  - main_theory: array of strings
- tags: array of strings

Identifier hints:
{hints_block}

Extracted text:
{text_block}"""
