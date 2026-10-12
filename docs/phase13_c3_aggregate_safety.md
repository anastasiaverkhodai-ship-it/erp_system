# 13-P1-C3-G2-C — Payroll Aggregate Safety

G2-C completed: 2026-10-12 (candidate assessment only).

C3 acceptance was subsequently extended with real PostgreSQL tests; see the
acceptance section below for the final validation status.

## Implementation

Added `app/services/payroll_deduction_aggregate_safety.py` and
`tests/hr/test_payroll_deduction_aggregate_safety.py`.

Inspected the existing legal limit service, verified limit calculator,
classification/evidence policy and actual payroll deduction result service.
The new read-only service reuses the evidence resolver and verified calculator.
It does not connect the candidate rules to actual payroll calculation.

The caller supplies an immutable snapshot for one company, employment contract,
effective date, UAH currency and statutory net base. Each instruction carries
both its proposed amount and any amount already withheld for the same period
and base. Prior withholding cannot be supplied as an anonymous scalar.

The service rejects the complete snapshot for:

- Duplicate/invalid instruction IDs and invalid monetary inputs (including
  negative fractions, non-finite numbers and sub-cent values).
- Missing instructions, company/contract mismatches, currency mismatches or
  instructions outside their effective dates.
- Missing, unverified or invalid evidence, including on a later item.
- Mixed legal categories, including categories with equal numeric caps.
- A prior-plus-proposed instruction amount above its candidate individual cap.
- A combined total above the candidate aggregate cap or statutory net.

No shortage allocation, category priority, legal rule changes, payroll writes,
transaction management or implicit autoflush are performed. A passing assessment
explicitly reports `authorizes_payroll=False`. Empty snapshots report no category
and zero capacity. Amounts use the existing candidate calculator's cap rounding.

## Validation

- 158 tests passed: 87 new tests and 71 existing deduction/evidence tests across
  ten test modules; no skipped or failed tests.
- New tests cover all ordered mixed-category pairs, aggregate boundaries,
  prior withholding, ordering invariance, malformed inputs, evidence failure,
  instruction/evidence SQL scoping and suppression of autoflush.
- An actual call to the existing payroll result service with a mocked session
  still produces deduction 3000 / payable 7000 on statutory net 10000; the new
  assessment does not intercept or change this behavior.
- New files compile; `git diff --check` passes.
- Payroll result service SHA-256 before/after is unchanged:
  `3bab0566dd91034947895fb7e51c3214a173b643c9fec432bcadaddb9eb73798`.
- Git HEAD before/after: `cb984bc6f951b1ac6e3d33ed64a40c9efb9614e4`.
- Read-only Alembic current before/after: `13d4a7b8c020`.
- Alembic head: `13d4a7b8c021`, still unapplied.
- No commit, push, migration or production payroll calculation was executed.

## Limits and next gate

The original 158-test gate used unit/contract and mocked-service regression
tests. The follow-up acceptance below adds real PostgreSQL verification on a
disposable copy. Production data and schema are not used for test writes.

The assessment covers the supplied snapshot only. It cannot establish that the
caller included all deductions, chose the right payroll period/net base, or
provided authoritative already-withheld amounts. It does not lock rows or create
a durable approval. Legal applicability, evidence temporal validity and a
consistent complete database snapshot remain prerequisites for future payroll
integration. Existing category percentages were reused as candidate arithmetic;
no new legal rules were asserted or approved.

Using the gate counts recorded in the referenced ERP conversation, after G2-C:

| Milestone | Remaining gates |
| --- | --- |
| C3 completion | 1: acceptance |
| Commit | 2: regression → commit |
| Push | 3: regression → commit → push |

Next gate: C3 Acceptance / Regression. Its exact identifier was not specified
in the available plan. The follow-up below records acceptance and regression separately. The user
subsequently authorized completion, commit and push; production migration
remains excluded.


## C3 acceptance follow-up — 2026-10-12

Added four PostgreSQL acceptance tests for persisted evidence lifecycle, exact
aggregate boundaries, cross-company/cross-contract rejection, the composite
foreign key, mixed-category rejection, and absence of implicit autoflush.

Review found and fixed two issues in the pending C3 package:

- Verification now refreshes the locked evidence row, preventing a stale
  cached pending instance from overwriting a completed verification. A second
  session verifies the evidence in the acceptance test, and the stale session
  must reject a repeated review without changing the original timestamp.
- Migration 021 refuses to discard historical evidence. It takes an exclusive
  table lock before checking for rows. Its empty-table downgrade/upgrade is
  tested against ORM metadata; an attempted populated-table downgrade leaves
  the evidence intact. These operations run only in a disposable test schema.

Acceptance result: **4 passed**, 10 deprecation warnings, 19.72 seconds.
The isolated database was removed. Log: `/tmp/erp-c3-acceptance.log`.

Full ERP regression through `scripts/run_postgresql_regression_copy.py tests`
finished with **4085 passed, 1 failed**, 8205 warnings in 1218.14 seconds.
The failure was `test_year_end_migration_roundtrip`: its dependency list omitted
existing migration 018, so it recreated `payroll_disbursements` without the
opening-debt column and associated constraints. Added 018 to the test's ordered
downgrade/upgrade dependencies, retaining the exact metadata comparison.
No production year-end or disbursement code was changed.
Log: `/tmp/erp-c3-full-regression.log`; the isolated database was removed.

After the full run, all 164 public tables / 1776 rows in the working database
matched the captured data hashes; revision remains 020 and evidence table 021
is absent. A final targeted rerun covers the corrected whole year-end module,
all deduction tests, opening disbursements, payroll disbursements and settlement
migration compatibility. No candidate rules are connected to production payroll.


Final rerun: **185 passed**, 602 warnings in 98.00 seconds; no failures or
skips. Log: `/tmp/erp-c3-final-regression.log`. The isolated database was removed.
The full 4086-test suite was not rerun from scratch after the one-line test
dependency correction: 4085 had passed in the full run, and the failing test
and related modules all passed in the 185-test rerun. Warnings are existing
deprecations and pytest marker warnings, not suppressed failures.

C3 candidate assessment acceptance and regression gates are complete; **0 gates
remain for this C3 scope**. Commit and push were explicitly requested by the
user. Production migration and applying legal limits to real payroll remain
outside this delivery. Next work is a separately scoped payroll integration
review covering legal applicability, complete period snapshots and concurrency.
The broader ERP Phase 13 is not declared complete.
