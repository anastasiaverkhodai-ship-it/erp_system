# Phase 13.14 — Combined D1 / 4DF / D6 Financial Review

## Implemented

Read-only PostgreSQL integration of existing contracts:

- Verified current payroll calculation sources.
- D1 dated USC source completeness and independent totals.
- 4DF accrued gross, PIT and levy comparison with payroll.
- D6 independently reviewed service evidence and applicability.
- Financial source schema inspection.
- Deterministic review digest and live re-verification.

## Important distinctions

D1 dated income-source amounts are not automatically equal
to the assessed USC contribution base.

4DF paid_amount is NET disbursement evidence. It is not
an approved value for statutory gross paid income.

A valid 4DF financial-group integrity manifest establishes
internal amount and calculation-ID coverage. It does not
independently authenticate each source fingerprint or
approve source binding and legal classifications.

When 4DF groups are absent, the result is NOT_VERIFIED.
When supplied groups reconcile, the status remains
AMOUNT_RECONCILED_SOURCE_BINDING_PENDING.

D6 may be COMPLETE, INCOMPLETE or MIGRATION_REQUIRED.
Its normative mapping remains NOT_APPROVED, including
when underlying personnel facts are independently reviewed.

The digest is not an immutable database snapshot or a
cryptographic signature issued by an independent authority.

No official D1, 4DF, D5 or D6 report rows are created.
No payroll data is posted, finalized or submitted.

OFFICIAL STATUTORY GENERATION = BLOCKED.
PHASE 13 = OPEN.
