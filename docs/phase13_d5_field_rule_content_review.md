# ERP 13.14 — D5 Normative Content and Field-Rule Review

## Scope

Source-bound extraction of candidate D5 rule passages from
existing local normative evidence.

This review extracts readable HTML, JSON and UTF-8 TXT.
PDF, DOC, DOCX and spreadsheet contents require a separate
format-specific extraction and source-verification step.

## Candidate rule topics

- Employment contract creation and termination.
- Permanent transfer and changes of position.
- Documented legal basis for termination.
- Changes between primary and secondary employment.
- Date of creation of a new workplace.
- Correcting and уточнюючий reporting.

These topics are search requirements, NOT an approved
statutory field schema.

## Evidence requirements

Every candidate excerpt includes its source file, original
file SHA-256, text offset, excerpt SHA-256 and explicit
REVIEW_REQUIRED status.

The per-run inventory is saved outside the Git worktree.

## Remaining normative acceptance

1. Verify source authenticity and the complete official annex.
2. Review PDF/DOC/DOCX/XLSX sources where applicable.
3. Confirm date-effective classifier revisions.
4. Resolve exact D5 form columns and report values.
5. Establish formal source-to-field mapping.
6. Verify correction and reversal rules.
7. Perform statutory positive and negative PostgreSQL E2E.

Official D5 generation remains BLOCKED.
Phase 13 remains OPEN.
