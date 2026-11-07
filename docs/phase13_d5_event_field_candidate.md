# ERP Phase 13.14 — D5 Event-to-Field Candidate Projection

## Implemented

Internal, non-statutory candidate projection for:

- Hiring.
- Permanent transfer.
- Termination with a supplied documented legal basis.
- Primary/secondary employment category changes.

The category-change review produces two candidate records,
with category transition 1/2 and transfer flag 1 on both.

## Provenance

Candidate records contain explicit company, employee,
contract and employment-event identifiers, source SHA-256
and a deterministic candidate digest.

The projector does not claim that a supplied SHA-256
is independently verified against an ERP database row.

## Important limitations

This is a candidate projection, not the official D5 writer.

Still required:

1. Bind every input to actual persisted employment events.
2. Verify current/reversed event state through PostgreSQL.
3. Resolve historical identity and profession records.
4. Verify exact classifier version for the event date.
5. Confirm legal meanings and permitted field values.
6. Implement remaining D5 categories and exceptions.
7. Verify correction and cancellation rules.
8. Obtain independent normative acceptance.

Official D5 generation remains BLOCKED.
Phase 13 remains OPEN.
