# Phase 13.14 — Internal reconciliation contract

## Scope

This service provides a common internal reconciliation representation
for D1, 4DF, D5, and D6.

It does not create official statutory rows or approve XML mappings.

### Delivered

- Company- and payroll-period-scoped row identity.
- Exact source type, ID, revision and document provenance.
- Decimal-safe financial components.
- Independent expected-total comparison supplied by callers.
- Source-bound correction lineage and terminal-revision totals.
- Deterministic canonical content and SHA-256 digest.
- Version-linked, immutable in-memory snapshot value objects.
- Explicit prohibition on regulatory approval or generated status.

### Not delivered

- Direct integration with all payroll source writers.
- Statutory field classification or XML schemas.
- Database persistence of these new snapshot value objects.
- Regulator acceptance lifecycle.
- Automatic approval of D5 classifiers or D6 special-service codes.
- KEP or submission.

The existing persisted regulatory snapshots remain authoritative
until a separately tested integration explicitly adopts this format.

The independent expected-source totals must be independently
calculated from verified payroll sources. Reusing the same projected
row values as expected totals would not provide independent proof.

Official generation must remain blocked until exact current
normative mappings and required rules are independently verified.
