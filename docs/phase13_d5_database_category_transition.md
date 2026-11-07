# Phase 13.14 — DB-Bound D5 Employment Category Transitions

## Delivered candidate functionality

The service verifies a persisted transfer event and historical
before/after employment-kind snapshots.

It requires an independently persisted earlier active HR event,
its professional evidence, historical position, current contract,
current professional evidence and employee identity.

A 1-to-2 or 2-to-1 transition produces two internal candidate
records, not official D5 report rows.

## PostgreSQL acceptance

Positive and negative tests cover:

- Real stored transfer and prior HR events.
- Two candidate category records.
- Previous and current professional evidence.
- Company isolation.
- Historical position mismatch.
- Reversed earlier HR source.
- Deterministic source digests.

## Outstanding limitations

This service requires explicit historical event-state fields.
Legacy HR events without those fields remain blocked.

The source text's normative date convention and classifier
authenticity remain unapproved. Accepted interval conventions
are review candidates only.

No official statutory report rows are created, submitted,
or authorized.

OFFICIAL D5 GENERATION = BLOCKED.
PHASE 13 = OPEN.
