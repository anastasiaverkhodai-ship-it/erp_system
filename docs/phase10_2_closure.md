# 10.2 — Closure: VAT settings, categories and validation

Date: 2026-09-16. Base commit: `1360daa0a9ab30e7fd97b08e714a7390d5e785f1`.
Scope: backend/API settings V1. Full Phase 10 remains open.

## Delivered

- Effective-dated company policies and counterparty registration histories,
  registration/evidence basis, append-only supported API and exact replay handling.
- Confirmation selects the historical company/counterparty records, stores scoped
  references, and validates rate/category, seller status and method prerequisites.
- Newly API-created companies start strict. Existing companies retain a visible
  legacy mode until explicitly configured; existing ledger data is not guessed.
- Exempt and out-of-scope categories remain separate from taxable zero-rated VAT.
  Non-payer treatment requires an explicit reason and legal basis.
- Special rates require legal basis; cash method requires company authorization
  plus the basis for the particular operation. Manual taxable recognition is blocked
  in strict mode. Non-payer positive-VAT purchases are blocked pending gross-cost
  accounting rather than treated as deductible input VAT.
- Generic rate versions support inclusive end dates and reject nonfinite values.
- Existing rounding is preserved and tested with fractional monetary examples.
- Draft create/update and responses carry the new fields. A real PostgreSQL
  edit/confirm test exposed stale ORM line collections: the API reload and locked
  invoice loader now refresh existing objects before returning/using them.

## Acceptance evidence

`tests/test_vat_settings_policy.py`: 23 cases covering invalid rates/dates,
expired-version behavior, category distinctions, special-rate basis, cash-method
prerequisites, non-payer safeguards, schema validation and cent rounding.

`tests/test_vat_settings_postgresql.py`: isolated schema, actual migration
round trips and metadata comparison, policy replay/conflict, effective date
selection, cross-company rejection and FK enforcement, draft PATCH persistence,
confirmation and stored policy IDs, taxable/exempt/non-payer results, invalid
supplier classification, historical-change protection and RBAC denial.
The surrounding transaction is rolled back and schema absence is checked.

Final verification:

- `RUN_POSTGRES_E2E=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short`: **3082 passed, 0 failed, 0 skipped**, 66.55 s.
- 1277 existing deprecation warnings (including warnings exercised by new scenarios); not suppressed.
- Targeted new scenarios: **24 passed**.
- Main DB and code revision: **e2a9c5d7f046**, migration applied.
- `alembic check`: **No new upgrade operations detected**.
- Offline migration SQL: PASS; private PostgreSQL downgrade/upgrade and metadata comparison: PASS.
- OpenAPI: settings, policy history/create and counterparty registration history/create paths present.
- `git diff --check`: PASS. No dependency changes.

**10.2 backend settings V1 COMPLETE within the explicit migration and scope boundaries below. Next: 10.3.**

## Limits / follow-up

The applicable statute and registration evidence are supplied by an authorized
operator. The module validates configuration and prerequisites, not every product
classification, exemption certificate or online registry entry. Existing companies
must be deliberately migrated from legacy mode. Historical corrections to settings
are not exposed as mutable records.

P10-01 (overlapping partial payment/supply) remains open. Rate/status selection
against actual first-event dates, including advances before invoice and changes
between events, belongs to 10.3. INPUT legal evidence/credit periods and non-payer
purchase gross-cost accounting require their own subsequent acceptance; no positive
input credit is silently granted by these settings. PН/RК and reporting remain later
subblocks. See [settings/API contract](vat_settings.md) for exact behavior, examples
and official sources, and [10.1 audit](phase10_1_audit.md) for the complete backlog.
