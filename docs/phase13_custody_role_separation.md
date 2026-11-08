# Phase 13.14 — Custody PostgreSQL Role Separation

## Covered tables

- payroll_payment_tax_secure_custody
- payroll_payment_tax_document_integrity
- payroll_payment_tax_document_reviews

## Security contract

The application role is `erp_user`.
The independent owner is `erp_snapshot_owner`.

The migration requires both roles to exist and rejects
superuser/BYPASSRLS runtime or inherited owner access.

Each table is restricted to SELECT and INSERT for
the application role, with no UPDATE, DELETE or
TRUNCATE.

An independent trigger rejects UPDATE and DELETE.

Sequence permissions allow ordinary INSERT operations
without transferring table ownership.

## Testing and deployment

The delivery tests the migration using an isolated
disposable PostgreSQL database only.

Production `erp_db` remains on its existing revision
until a separately authorized migration rollout.

The role security evaluator can be called on an actual
application SQLAlchemy session. Its existence alone
does not mean all API entry points invoke it.

The positive custody/review E2E tests must also pass
after the ownership and privilege changes.

## Remaining blockers

- Production migration rollout and operational key setup
- Authentication and authorization coverage of all API paths
- Independent external issuer/source authenticity
- Actual payment-specific PIT and levy withholding
- Regulatory field mapping and D1/4DF/D5/D6 acceptance
- Phase 13.15 final regression

Official statutory generation remains BLOCKED.
