# ERP Phase 13.14 — D5 Source Correction Chain

## Delivered

Read-only PostgreSQL verification of an original HR event,
its persisted reversal, and an explicitly linked replacement
for hire and transfer source corrections.

The review checks event identifiers, company/contract scope,
unique reversal, active replacement, professional evidence,
dated source history, and deterministic correction digest.

## Limits

This is an internal HR-source correction review.

It does not classify official D5 correction rows,
implement statutory cancellation/clarification markers,
authorize a statutory correction, or generate report rows.

Termination source corrections, historical category
transition corrections, cross-period corrections and
regulatory report snapshot revisions require separate
rules and acceptance.

OFFICIAL D5 GENERATION = BLOCKED.
PHASE 13 = OPEN.

## Additional correction uniqueness acceptance

The correction-chain reviewer also rejects multiple persisted
replacement events claiming the same original HR source.
Original and replacement professional evidence must refer
to the same employee.

A competing replacement is tested against a real isolated
PostgreSQL database, not only an in-memory model.

These controls do not authorize official D5 correcting rows.
Normative applicability and correction markers remain
unapproved and fail closed.
