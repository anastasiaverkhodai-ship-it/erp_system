# Phase 13.14 — PostgreSQL Restricted Runtime Role Proof

## Existing roles

- `anastasia`: administrative superuser, not suitable
  for normal ERP application queries.
- `erp_user`: LOGIN role without SUPERUSER/BYPASSRLS.
- `erp_snapshot_owner`: NOLOGIN protected-table owner.
- `erp_guard_owner`: NOLOGIN guard-table owner.

## Restricted role gate

The service checks the actual PostgreSQL current_user
and privileges on payroll_regulatory_correction_snapshots.

The restricted application connection must:
- authenticate/use `erp_user`;
- be neither superuser nor BYPASSRLS;
- not own or inherit the owner of the snapshot table;
- have SELECT and INSERT, but no UPDATE, DELETE,
  or TRUNCATE;
- see an enabled immutable UPDATE/DELETE trigger.

The gate must execute on the application's actual
SQLAlchemy session, never on a separate administrator
psql connection.

## Deployment status

This delivery does not modify production users, roles,
passwords, owners or existing table grants.

`SET ROLE erp_user` provides a database privilege proof.
It is not confirmation that the running FastAPI process
uses the same role.

The new custody/integrity/review tables are not yet
deployed to production, so their production-role
privileges cannot be certified here.

Owner separation for all other protected ERP tables,
production migration deployment, external encryption
key management and statutory evidence acceptance
remain pending.

Official generation remains BLOCKED.
