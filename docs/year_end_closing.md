# Phase 11.9 — Calendar year-end closing

## Accounting scope

This is a GL closing workflow for the company's existing Ukrainian chart of
accounts. It creates a balanced journal on December 31, closes income/expense
balances through financial-result accounts (79), and transfers the result to
retained profit/uncovered loss (44). Original amounts exist only in canonical
`journal_entry_lines`; `year_end_closings` holds lifecycle and request identity.

Reference: Ministry of Finance Instruction No. 291, accounts 79 and 44:
https://zakon.rada.gov.ua/laws/show/z0893-99

It does not calculate income tax, allocate production overhead, produce statutory
financial statements, or certify subledger reconciliation. Those operations must
be completed separately. Previously identified unimplemented accounting-control
families are not marked complete by year-end closing.

## Working-account selection

Supply company-owned active, postable accounts explicitly. No account creation,
remapping, or template replacement occurs as a side effect of closing.

For general_291, the supported source-to-result groups are:

| Source family | Result family |
| --- | --- |
| 70, 71, 90, 92, 93, 94, 98 | 791 (or aggregate 79) |
| 72, 73, 95, 96 | 792 (or aggregate 79) |
| 74, 97 | 793 (or aggregate 79) |

Class 8, account 91 and other unsupported source families must be allocated or
settled before closing. Missing mappings for any nonzero income/expense balance
block execution. General-chart profit/loss destinations are 441/442 respectively
(or aggregate 44). The simplified_186 chart also uses explicit postable 79/44
working accounts. Result and destination accounts must have equity classification.

The closing uses signed balances, including contra revenue and existing 79
balances. Nonzero income/expense/79 balances from earlier years are rejected:
close or correct the earlier year first. No balance-sheet account is cleared.

## API

Prefix: `/api/v1/companies/{company_id}/year-end-closings`.

| Method/path | Required permissions |
| --- | --- |
| POST `/{year}/preview` | `journal_entries.read` |
| POST `/{year}/close` | `accounting.periods.manage` AND `journal_entries.post` |
| GET base path or `/{closing_id}` | `journal_entries.read` |
| POST `/{closing_id}/reverse` | `accounting.periods.manage` AND `journal_entries.reverse` |

Preview body (IDs are illustrative and must be resolved within the company):

```json
{
  "profit_account_id": 13,
  "loss_account_id": 14,
  "mappings": [
    {"source_account_id": 10, "result_account_id": 12},
    {"source_account_id": 11, "result_account_id": 12}
  ]
}
```

The read-only preview returns signed `profit` (negative means loss), the proposed
journal lines and `preview_fingerprint`. For `close`, submit the same configuration
plus that fingerprint and a nonblank unique `request_key`. A balance change after
preview requires reviewing a fresh preview. Preview calculates amounts; execute
also checks period and draft preconditions under locks.

Same-key/same-payload retries return the existing closing, including its reversed
status if it has since been reversed. Changed payload with the same key is a
conflict. A replacement closing requires a new request key and a fresh preview.
An empty year has a closing identity and locked periods but no artificial zero
journal. GET includes the original and reversal journal IDs where applicable.

## Preconditions and locking

- Company must be active; only completed calendar years from 2000–2100 can close.
- All twelve calendar-month periods must exist with correct boundaries.
- All earlier existing periods and January–November must already be closed.
- December must be open for the closing journal; execution closes it atomically.
- No unresolved draft journals may remain on or before the closing date.
- GL must balance and every nonzero account must belong to the company.
- Company and ordered period locks serialize execution with competing closings,
  new historical periods and ordinary journal posting. A posting waiting on
  December cannot enter the year after closing commits.
- Opening/creating an accounting period in or before a closed year is blocked.
  Generic journal posting, editing, deletion and reversal cannot bypass the
  year-end lifecycle. Database source exclusivity and uniqueness protect links.

## Reversal and reports

Reverse the latest active closed year first. The workflow temporarily reopens
December, posts the canonical reversal on the same December 31, records actor and
time, and leaves December open for corrections. Earlier months remain closed;
they may then be explicitly reopened using normal period permissions. If any
step fails, the API rolls back the journal, lifecycle and period changes together.
Reversal retries return the already-reversed identity.

This is a historical reopening workflow, not a next-year adjustment. It changes
December 31 and following opening balances; already-issued external reports are
not automatically regenerated. Subsequent closed years must first be reversed.

Existing GL/account-card/trial-balance queries provide next-year balances without
creating duplicate January opening entries. Income/expense/79 balances become
zero; balance-sheet balances and the result in 44 carry forward normally.

## Migration and verification

Migration: `bf330ab29fd9 → c0441bc30ae0`. No previously applied migration is edited.
Upgrade adds lifecycle/provenance, active-year uniqueness, tenant foreign keys and
source exclusivity. Downgrade removes year-end provenance and must only be used
as a planned database rollback; business corrections use reversal.

Tests cover profit, loss, zero result, empty year, missing mappings, account
validation, prior-year balances, unresolved drafts, stale previews, permissions,
API rollback, concurrent retries, competing posting, dependent closed years,
GL/trial-balance carry-forward and real PostgreSQL migration roundtrip.

```sh
PYTHONDONTWRITEBYTECODE=1 RUN_POSTGRES_E2E=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
.venv/bin/python -m alembic check
```

### Closure verification — 2026-09-23

- Full regression with PostgreSQL enabled: **3484 passed**, no failures (306.45 s).
- Additional contra-revenue/existing-result scenarios for general and simplified
  charts: **2 passed**. No production-code changes followed the full run.
- Database and code head: `c0441bc30ae0`; Alembic reports no schema drift.
- PostgreSQL upgrade/downgrade, opening-migration chain compatibility, concurrent
  closing/reversal, blocked competing posting, and rollback after an injected
  reversal failure all passed.
- Existing project deprecation warnings remain; closing does not certify the
  broader control families or statutory reporting deferred to later work.
