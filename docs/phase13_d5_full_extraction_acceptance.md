# ERP Phase 13.14 — Full D5 Normative Source Extraction

## Purpose

Extract actual source text and tables with verifiable locators.

Supported extraction:

- HTML and JSON source content.
- PDF text with page references.
- DOCX paragraphs and tables.
- XLSX worksheet rows and cell coordinates.
- Legacy DOC through available macOS textutil.
- UTF-8 text files.

PDF extraction uses an available PDF parser. Image-only scans
may remain without selectable text; no OCR-based regulatory
claims are authorized.

## Evidence

The complete per-run source extraction is stored outside
Git under ERP_BACKUPS. Each successfully extracted document
has its own source SHA-256 and source-bound content blocks.

Each extraction error or missing textual content is explicit.

## Outstanding acceptance

- Independently validate the official source and edition.
- Validate extracted tables against the original document.
- Resolve classifier amendments and effective dates.
- Verify exact D5 field identifiers and allowable values.
- Verify all D5 event-to-field and correction rules.
- Test the actual field mapping on PostgreSQL records.
- Obtain documented normative acceptance.

Extraction is not statutory approval.

Official D5 generation remains BLOCKED.
Phase 13 remains OPEN.
