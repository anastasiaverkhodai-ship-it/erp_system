# ERP 13.14 — D5/D6 Regulatory Review Bridge

## Delivered

A review-only integration between:

1. Existing D5/D6 regulatory source workflow.
2. Verified database-bound D5 candidate snapshots.

It creates a digest-bound review envelope and checks
that both source-review systems can independently
rebuild their evidence from the current PostgreSQL data.

## Acceptance

Real isolated PostgreSQL cases check source validity,
integrity tampering, company isolation, and rejection
after a professional source change.

## Important limitations

This is an internal review bridge, not an amendment
to the official regulatory report generator.

The workflow review and D5 candidates may have different
period inclusion policies. Full per-event coverage and
period reconciliation still require acceptance.

The bridge is not an immutable database snapshot.
Its digest is not a digital signature.

It performs no statutory filing, official correction
generation, report approval, or KEP operation.

The caller must provide a consistent database transaction
when stronger concurrent-read guarantees are required.

OFFICIAL D5 GENERATION = BLOCKED.
PHASE 13 = OPEN.
