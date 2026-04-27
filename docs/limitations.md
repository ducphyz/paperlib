# Limitations

These are v1 design limits, not runtime errors.

## No OCR

`paperlib` does not perform OCR.

Scanned PDFs and image-only pages will have low or zero text extraction. They
may be rejected during validation, moved to `failed/`, or classified with
low-quality extraction such as `scanned` or `low_text`.

## PDF Text Extraction

Text extraction uses `pdfplumber` and depends on the text layer embedded in the
PDF.

Known limitations:

- equations may extract poorly
- multi-column layouts may be imperfect
- symbols may be missing or distorted
- ligatures are normalized where possible, but may still be imperfect

## Metadata

DOI and arXiv detection is regex-based. It can miss identifiers or capture only
the first detected match.

In v1, `title`, `authors`, and `journal` require AI to be populated. Without AI,
these fields remain `null` unless manually edited.

v1 does not call Crossref, the arXiv API, Semantic Scholar, or any other
external metadata lookup service.

The year heuristic is conservative. It may return `null` rather than risk a
false positive.

## AI

AI output may fail JSON parsing or validation. When that happens, the affected
summary is marked `failed` and ingest continues.

AI may return `null` for unknown fields. This is expected and preferable to
fabricating metadata.

AI output should not be treated as authoritative without review. Use locked
fields to protect reviewed metadata or summaries.

AI never overwrites locked metadata fields or locked summaries. AI also does
not set or overwrite `metadata.year`.

## Duplicates

Exact duplicates are detected by full file hash.

Paper-level alias duplicates are detected by DOI or arXiv ID.

v1 does not implement fuzzy duplicate detection. Similar PDFs without matching
hashes, DOI, or arXiv IDs may become separate records.

## Search and RAG

v1 does not implement:

- embeddings
- chunking
- semantic search
- question-answering over papers

SQLite is an operational index, not a semantic search system.

## Operations

`paperlib` is local filesystem oriented.

v1 does not include:

- a multi-user permission system
- a GUI
