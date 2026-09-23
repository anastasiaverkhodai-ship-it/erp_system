# Phase 11 — Ukrainian accounting core: closure and acceptance

## Scope

Phase 11 completes the agreed accounting-core scope using the existing chart,
canonical journal, posting/reversal engine and accounting periods. It does not
introduce a second ledger or claim that every subledger control is implemented.

The 11.7 contract explicitly deferred unsupported source-to-GL comparators.
That boundary remains: the implemented OUTPUT VAT comparator is exposed alongside
five honest `not_implemented` results. Completion of this phase means the agreed
core and its acceptance checks are complete, not that the product is certified
for every accounting, tax, production or statutory-reporting scenario.

## Plan and delivered evidence

| Block | Delivered result | Evidence |
| --- | --- | --- |
| 11.1 | Architecture audit | Existing chart, journals, periods and source provenance identified |
| 11.2 | Exact implementation contracts | Canonical `app.core.database.Base`, models and posting behavior verified |
| 11.3 | Scope classification | Reuse existing foundation; build missing reports/opening/year-end workflows |
| 11.4 | GL and account card | `d46041f`; company/date/account scope, original plus reversal, no drafts |
| 11.5 | Trial balance / ОСВ | `d07dd3d`; opening/period/closing debit and credit balances |
| 11.6 | Accounting-period locking | `04b60b4`; consistent open/closed state and locked transitions |
| 11.7 | Consolidated control surface | `ca007b8`; verified OUTPUT VAT plus explicit unavailable families |
| 11.8 | Opening balances | `0417fa3`; immutable provenance, idempotent lifecycle, GL-based amounts |
| 11.9 | Year-end closing | `f5098a9`; reviewed preview, signed result, year lock and controlled reversal |
| 11.10 | Stabilization and acceptance | Current change: coverage semantics, fresh locked state, end-to-end verification |

See [Opening balances](opening_balances.md) and [Year-end closing](year_end_closing.md)
for request formats, preconditions, permissions and correction workflows.

## Final hardening

### Honest consolidated-control status

`GET /api/v1/companies/{company_id}/accounting-controls` now distinguishes:

- `coverage_complete`: every listed family has an implemented comparator;
- `checked_families_matched`: all available comparators passed, with at least one available;
- `matched`: complete coverage AND every implemented comparator passed;
- `status`: `mismatch` if any available control fails; otherwise `incomplete` when
  coverage is missing; otherwise `matched`.

**Compatibility change:** `matched=true` previously meant only that the available
subset passed. Consumers needing that previous meaning should use
`checked_families_matched`. With the current one-of-six coverage, an otherwise
passing report has `matched=false`, `status="incomplete"`,
`checked_families_matched=true`, `coverage_complete=false`. Family amounts and
individual results remain available. A real mismatch takes precedence over the
incomplete coverage status.

Missing or invalid chart-role configuration returns HTTP 409; invalid date
ranges return HTTP 422. Reports never silently fabricate missing mappings.

### Fresh state under row locks

Posting/reversal, manual draft mutation and period transitions explicitly refresh
ORM entities after acquiring row locks. This prevents an earlier cached draft,
line collection or open-period state from overriding a later committed change.
GL/account-card reads refresh entity fields so report dates and account labels
reflect the database query rather than stale session objects.

## Acceptance coverage

`tests/test_phase11_stabilization_postgresql.py` exercises real PostgreSQL:

- another session changes draft amounts before posting: fresh balance validation;
- another session posts a previously cached draft: reversal uses posted status and amounts;
- a posted journal cannot be edited through a cached draft object;
- period transitions reject already-completed concurrent transitions;
- opening → ordinary operations → GL/card/ОСВ → year close → reversal → replacement close;
- GL/card/ОСВ totals and each account balance agree across year boundaries;
- earlier historical balances survive later reversal dates;
- reports run under `SET TRANSACTION READ ONLY`;
- HTTP permissions isolate companies and report configuration/date errors cleanly;
- report dates and account names refresh after a concurrent committed change.

The full suite additionally covers source-specific accounting, PostgreSQL
constraints, migration roundtrips, VAT, stock, AR/AP, opening/year-end retry and
rollback behavior. Passing those tests does not turn deferred reconciliation
families into implemented controls.

## Local database audit

A read-only audit checked posted journal balance/line count, tenant account links,
original/reversal links, period-state consistency, opening/year-end provenance,
and active year-close period locks. No business data was repaired by this gate.

One pre-existing negative-test draft references a foreign-company account. It is
unposted and excluded from GL/ОСВ; canonical posting rejects such accounts. It must
be resolved as test data before attempting a real year close that includes its
date, because year closing correctly rejects unresolved drafts. Posted-entry
integrity and test-fixture cleanup are separate acceptance concerns.

## Remaining product work outside this agreed phase

- Verified source-to-GL comparators for AR, AP, cash/bank, inventory and INPUT VAT.
- Detailed subledger migration alongside the GL opening-balance workflow.
- Production/overhead allocation, income-tax calculation and statutory financial
  statements beyond the implemented GL closing workflow.
- Official DPS XSD verification and submission/KEP/transport remain outside this
  accounting phase; existing canonical XML is not represented as official validation.

## Reproducible commands

```sh
PYTHONDONTWRITEBYTECODE=1 RUN_POSTGRES_E2E=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
.venv/bin/python -m alembic check
git diff --check
```

The schema remains `c0441bc30ae0`; 11.10 requires no new migration.

## Final verification — 2026-09-23

- Full regression with PostgreSQL enabled: **3495 passed**, no failures or skips
  (340.81 seconds). Existing deprecation warnings remain outside this gate.
- Alembic database/code head: `c0441bc30ae0`; no schema drift.
- Read-only database audit repeated after the full suite: zero unbalanced/empty
  posted journals, foreign-account posted lines, invalid reversal links, missing
  reversals, inconsistent period states, invalid opening/year-end provenance,
  or open periods under an active year closing.
- The single pre-existing foreign-account negative-test draft remains unposted;
  this gate did not alter business records or remove test data.
- Agreed Phase 11 scope: complete. Deferred comparator families and other product
  boundaries remain explicitly documented above.
