# Phase 13 — PostgreSQL Role Separation

Status: NOT IMPLEMENTED. Gate 1 OPEN.

## Current verified security boundary

- erp_user owns protected journal tables.
- erp_user owns the current database and can create schema objects.
- Six journal protection triggers are enabled.
- Two SQL owner-trigger bypass scenarios are confirmed.
- The source database has not been modified.

## Required final architecture

- erp_schema_owner: NOLOGIN, owns schema objects.
- erp_migrator: separate login with controlled migration privileges.
- erp_runtime: application login, without object ownership, DDL,
  TRIGGER-management authority, or inherited owner privileges.
- Alembic reads ERP_MIGRATION_DATABASE_URL.
- The application continues to read its existing runtime database URL.

## Safe implementation sequence

1. Use a separate PostgreSQL cluster, not just another database.
2. Restore existing database data into the isolated cluster.
3. Inventory ownership, grants, sequences, functions, and role membership.
4. Create owner, migrator and runtime roles only in that cluster.
5. Transfer object ownership, verify defaults and dependencies.
6. Grant required runtime DML and sequence access.
7. Prove runtime cannot disable security triggers or change ownership.
8. Prove runtime cannot assume the migration owner's role.
9. Run migrations with migrator credentials and ERP with runtime credentials.
10. Run PostgreSQL Payment, Payroll and full security regression.
11. Validate data-preservation and rollback.
12. Only then prepare source-database rollout.

No production role changes are authorized by this document.
Do not store credentials in version-controlled files.
