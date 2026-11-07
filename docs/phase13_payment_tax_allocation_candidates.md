# Phase 13.14 — Persisted payment-tax allocation candidates

## Implemented

A database table stores draft tax allocation candidates,
each tied through composite foreign keys to the company,
payroll calculation, actual disbursement and statutory
PIT or military-levy line.

The writer checks actual active posted payment evidence,
the corresponding statutory source, currency, amount
precision, employee calculation scope, duplicate
allocation and accrued-tax ceilings.

A payroll calculation row lock serializes concurrent
candidate creation.

The writer does not commit the outer transaction.

## Critical distinction

The allocation amount is entered with an evidence reference.
Neither the reference string nor the matching statutory
accrual amount independently proves that the specified
tax was actually withheld on the payment date.

All candidates are permanently draft-only under this
migration. They cannot be promoted to approved status.

The existing `payroll_withholding_batches` refer to
non-statutory deductions and are not substituted for
PIT or military levy.

## Acceptance still open

- Independent verified payment-level tax withholding evidence
- Review/approval workflow and reversal or correction chain
- Dated tax attribution to 4DF reporting month
- Official income classification and D6 basis mapping
- Phase 13.15 full regression

OFFICIAL GENERATION = BLOCKED.
PHASE 13 = OPEN.

## PostgreSQL migration-test setup

The PostgreSQL tests apply the actual Alembic `upgrade()`
for revision `p13wtalloc261107` using the isolated test
database connection.

Migration DDL runs inside the test transaction, not
against the source `erp_db`. The tests verify the
new schema and exercise the writer before rollback.

The test explicitly refuses to apply its migration
when PostgreSQL reports database name `erp_db`.

A passing test validates the candidate persistence
mechanism only. It does not approve real payment-level
tax withholding or statutory gross paid.

## PostgreSQL search_path acceptance correction

The isolated test fixture can use its own PostgreSQL
schema. The Alembic upgrade creates an unqualified
table in the active connection schema.

The test therefore verifies the unqualified relation
through the current PostgreSQL search_path rather
than assuming the table resides in `public`.

If the relation remains missing, the test prints
`current_schema()` and `SHOW search_path` and fails
before commit or push.

No production migration is applied by this test.
