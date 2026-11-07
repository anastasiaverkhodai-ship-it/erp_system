# ERP Phase 13.14 — D5 Normative Acceptance Register

Date: 2026-11-07

## Current status

- D5 HR source intake: implemented.
- D5/D6 review inventory: implemented.
- Independent PostgreSQL inventory E2E: passed previously.
- Normative source candidate validation: implemented in this delivery.
- Independently approved source documents: 0.
- Official statutory D5 field mapping: NOT APPROVED.
- Official D5 generation: BLOCKED.
- Phase 13: OPEN.

## Required normative evidence

1. Profession classifier with independently verified versions.
2. All applicable profession classifier amendments.
3. Applicable D5 filing instructions for each reporting period.
4. Official field-level D5 mapping and event codes.
5. Applicable correction, amendment and reversal rules.

Existing documents and source acquisition files under
docs/normative_evidence/ are preserved without modification.
They are not automatically admitted as authoritative evidence.

## Outstanding acceptance

- Independently verify complete document content.
- Confirm effective dates against the governing texts.
- Bind verified SHA256 digests to actual acquired documents.
- Resolve historical classifier amendments and supersession.
- Review D5 event and profession field mappings.
- Add official field-level positive and negative PostgreSQL E2E.
- Integrate approved evidence into regulatory readiness only
  after reviewing the existing uncommitted readiness changes.
- Keep statutory generation BLOCKED until acceptance.

## Safety controls

This delivery does not change payroll readiness service,
production database migrations or existing user evidence.
Candidate coverage alone does not imply approval.
No official D5 rows are produced.
