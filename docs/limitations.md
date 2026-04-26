# Limitations

These are v1 design limits, not runtime errors.

## PDF Text Only

`paperlib` extracts text with `pdfplumber`. It does not perform OCR. Scanned
PDFs or image-only pages may be rejected during validation or classified as
low-quality extraction.

## No External Metadata APIs

v1 does not call Crossref, the arXiv API, Semantic Scholar, or other external
metadata services. DOI and arXiv IDs are detected with regular expressions from
the filename and extracted text.

## Conservative Metadata

Unknown metadata is stored as `null`. `paperlib` must not fabricate title,
author, journal, year, DOI, or arXiv values.

AI can fill selected fields and summaries, but it never overwrites locked fields
and never overwrites `metadata.year`.

## No Search/RAG Layer

v1 does not implement embeddings, chunking, vector search, RAG, or a GUI.

The SQLite database is an operational index, not a semantic search system.
