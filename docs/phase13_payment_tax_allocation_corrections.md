# Phase 13.14 — Source-bound draft allocation corrections

Corrections are stored in a separate append-only chain.

The original candidate and review/revocation events are
never overwritten or deleted.

A correction is permitted only after explicit revocation
of the original candidate. Each subsequent correction
must reference the latest correction.

The effective allocated amount is the most recent amount
in the chain. An original revoked but not replaced still
reserves its original amount.

The writer serializes on the underlying payroll calculation
and checks statutory source ceilings.

All correction records remain INTERNAL DRAFT ATTRIBUTION.
They do not verify actual payment-specific PIT or military
levy withholding, approve gross paid, or authorize reports.

Application and PostgreSQL role separation remain necessary.
The database immutability triggers must not be relied upon
when the application role owns the protected tables.

OFFICIAL GENERATION = BLOCKED.
PHASE 13 = OPEN.

## PostgreSQL E2E shared fixture recovery

The common PostgreSQL payment fixture applies the real
Alembic migrations in this order within an isolated
test transaction:

1. `p13wtalloc261107` — candidates
2. `p13wtrev261107` — review/revocation events
3. `p13wtcorr261107` — correction history and protection

The correction-specific test recognises an already
installed correction table and does not rerun its DDL.

The fixture refuses to run its migration installation
when the database name is `erp_db`. All test data and
test DDL are rolled back or removed with disposable
PostgreSQL infrastructure.

This change does not constitute normative validation
of actual withholding or 4DF gross paid.
