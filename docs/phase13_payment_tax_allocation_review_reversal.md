# ERP Phase 13 — Payment-tax draft review and reversal

## Implemented

- Append-only `reviewed` and `revoked` events.
- Composite FK to original company-owned draft candidate.
- Actor and evidence reference captured per event.
- Separate-person guard for review.
- Idempotent event replay with conflict detection.
- Single review/revocation event per candidate.
- Database trigger blocks UPDATE and DELETE of events.
- Reuse of a revoked draft is rejected.
- Event history is reconstructed without overwriting the draft.
- Real PostgreSQL negative tests.

## Important controls

`reviewed` is an internal document review status only.
It does not mean actual tax withholding was independently
verified for a salary payment.

The original candidate allocation remains draft-only,
with its original statutory ceiling reserved. Revocation
does not automatically free that amount for reallocation.

There is no automatic legal approval, normative gross-paid
attribution, statutory report generation or budget remittance
authorization from these events.

## Remaining acceptance work

- Independently evidenced payment-specific tax withholding.
- Source-bound replacement/correction workflow and ceilings.
- Dated 4DF gross-paid allocation and report correction chain.
- Normative D6 verification.
- Full Phase 13.15 acceptance.

Official generation remains BLOCKED.
Phase 13 remains OPEN.

## PostgreSQL E2E recovery

The candidate test fixture now executes both real
Alembic revisions in order:

1. `p13wtalloc261107`
2. `p13wtrev261107`

Both are installed exclusively on the isolated test
connection, not on production `erp_db`.

Review tests recognize the already installed event
table and avoid executing the second migration twice.

Event validation checks terminal revocation before
the self-review rule. Self-review remains forbidden
for active draft candidates.

These changes do not provide independent evidence
of actual PIT or military levy withholding.
