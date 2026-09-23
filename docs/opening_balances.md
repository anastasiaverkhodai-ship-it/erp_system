# Phase 11.8 — Opening balances

## Scope

Opening balances are company-scoped general-ledger journals. `opening_balances`
stores identity, date, actor and request identity; amounts live exclusively in
`journal_entry_lines`. There is no parallel opening-balance ledger.

This API imports GL balances. It does not create stock quantities, customer or
supplier settlement documents, VAT documents, or detailed subledger balances.
Those require their respective domain workflows and reconciliation before an
entire company migration can be considered complete.

## API

Prefix: `/api/v1/companies/{company_id}/opening-balances`.

| Method/path | Permission | Result |
| --- | --- | --- |
| POST base path | `journal_entries.create` | Create immutable draft, HTTP 201 |
| GET `/{id}` | `journal_entries.read` | Original lines and lifecycle status |
| POST `/{id}/post` | `journal_entries.post` | Canonical journal posting |
| POST `/{id}/reverse` | `journal_entries.reverse` | Canonical opposite-entry reversal |

Create example (replace account IDs with company-owned postable accounts):

```json
{
  "request_key": "migration-2026-opening-001",
  "opening_date": "2026-08-31",
  "description": "Approved opening trial balance",
  "lines": [
    {"account_id": 101, "debit": "100.00"},
    {"account_id": 202, "credit": "100.00"}
  ]
}
```

Reversal body: `{"reversal_date": "2026-09-01"}`.

## Guarantees and correction workflow

- At least two lines, positive balanced totals and at most two decimal places.
  Each line has exactly one positive side. Accounts must be active, postable and
  belong to the company. Future opening dates are rejected.
- A nonblank `request_key` is required. Retry with the same key and validated
  payload returns the existing identity; a changed payload returns HTTP 409.
  Preserve the original payload, including decimal representation, when retrying.
- Concurrent create, post and reverse requests are serialized. Repeated posting
  returns the posted journal. Reversal replay on the same date returns the
  existing reversal; another date is a conflict. Reposting a reversed journal is
  rejected. Reversal cannot predate the opening or be future-dated.
- Posting and reversal require the corresponding accounting period to be open.
  The period is locked and refreshed before checking its state.
- Generic journal editing/deletion cannot alter opening journals. Correct a
  posted opening by reversing it and creating a replacement with a new key.
  An erroneous draft can remain unposted; it has no accounting effect.
- Original and reversal journals retain `opening_balance_id`. Tenant-scoped
  foreign keys, source exclusivity and unique original identity protect provenance.
  Only authorized company roles can access endpoints.
- API writes construct responses before committing; failures roll back the
  transaction. Services never commit independently.

## Reports and dates

Drafts do not enter GL, account card or trial balance. A posted opening appears
as turnover on `opening_date`; for a report starting the following day, it appears
in the opening balance. Choose the approved cutover date accordingly. Reversal
adds opposite turnover on its date, preserving earlier historical reports.

## Database migrations

`230ce697c07a → af229fa18ec8 → bf330ab29fd9`.

The first migration adds provenance. Its downgrade was corrected to remove only
objects created by its upgrade; the applied upgrade is unchanged. The second
adds request identity and a tenant-scoped link back to the journal. Existing
records receive `legacy:<id>` keys and retain their journal links. Downgrading
hardening discards request identity history; downgrading the foundation removes
opening provenance. Database rollback is not document correction.

## Verification

`tests/test_opening_balances.py` covers invalid payloads, authenticated routes,
immutable journals and rollback before commit on response failure.
`tests/test_opening_balances_postgresql.py` covers real PostgreSQL lifecycle,
GL/account card/trial balance, company isolation, invalid accounts, closed periods,
HTTP permissions, concurrent retries, migration roundtrip and legacy preservation.
PostgreSQL fixtures use disposable isolated schemas.

```sh
PYTHONDONTWRITEBYTECODE=1 RUN_POSTGRES_E2E=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
.venv/bin/python -m alembic check
```

### Closure verification — 2026-09-23

- Full suite with PostgreSQL enabled: **3457 passed**, no failures (139.88 s).
- After explicitly ordering the cyclic provenance foreign keys with `use_alter`,
  targeted lifecycle, source-contract and schema tests: **62 passed**, no failures.
- Alembic current/head: `bf330ab29fd9`; `alembic check`: no new upgrade operations.
- Real PostgreSQL migration roundtrip, legacy backfill and concurrent request
  tests passed. The new table-order warning was eliminated; existing project
  deprecation warnings remain outside this block.
