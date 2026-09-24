# Phase 11.8 — Opening balances

## Scope

Opening balances are company-scoped general-ledger journals. `opening_balances`
stores identity, date, actor and request identity; amounts live exclusively in
`journal_entry_lines`. There is no parallel opening-balance ledger.

The original base create endpoint imports GL balances. It does not create stock quantities, customer or
supplier settlement documents, VAT documents, or detailed subledger balances.
Use the detailed cutover extension below to materialize stock and customer/supplier
debt together with GL, or attach it to a posted opening.

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

## Detailed cutover extension

The original GL-only endpoints remain supported. Two additional endpoints use the
same transaction as materialization into canonical warehouse and debt subledgers:

- `POST /packages/create`: `{ "opening": <OpeningBalanceCreate>, "details": ... }`;
  requires both journal create and post permissions; creates/posts exactly one journal.
- `POST /{id}/details`: `{ "stock": [...], "debts": [...] }`; post permission;
  attaches to an existing posted opening without a second GL journal.
- Existing `GET /{id}` includes the package, physical document ID and open-item IDs.

Detail shape (IDs are company-owned references, amounts are UAH):

```json
{
  "stock": [{"product_id": 1, "warehouse_id": 1, "quantity": "10", "unit_cost": "12.5000"}],
  "debts": [{"reference": "LEGACY-INVOICE-1", "counterparty_id": 1,
    "contract_id": null, "item_type": "receivable", "document_date": "2026-08-01",
    "due_date": "2026-08-15", "amount": "60.00"}]
}
```

The complete detail must match the opening's inventory 281 debit, customer 361 debit
and supplier 631 credit exactly, using configured company role accounts. Each stock
line is rounded to cents. Original debt dates can precede cutover (including overdue
balances); historical aging includes them only from cutover onward. Advances and
other subledgers are not represented as customer/supplier debt in this format.

Stock materialization uses the existing FIFO or moving-average receipt engine.
Active or later warehouse history for the same product/warehouse rejects import:
backdating cutover into operational history would invalidate valuation. A replacement
is supported after reversal, with cutover no earlier than the reversed stock history.

Debts have explicit opening provenance and no fabricated trade invoice. They can be
settled by confirmed payments; clearing uses Dr681/Cr361 or Dr631/Cr371 and creates
no VAT recognition. Original invoice evidence remains outside this cutover import.

One immutable package per opening: identical retry is a no-op; changed data conflicts.
Company serialization protects concurrent attachment. Closed periods, foreign or
inactive master data, mismatching GL/detail and duplicate source lines reject the
whole transaction. Legacy closed contracts may be referenced for their remaining debt.

Use opening reversal for corrections. Direct physical-document/GL reversal is blocked
for detailed openings. Active settlements must be reversed first; consumed FIFO stock must
be restored by the operational reversal workflow. Moving-average reversal retains the
existing strict chronology guard: any later inventory movements block reversal even
if those later movements were subsequently reversed; this extension does not replay
historical moving-average costs. Reversal cannot predate settlement
history. Opening, stock and debt reversal commit together; earlier reports retain history.
A new request key creates the corrected replacement.

Migration `d1552cd41bf1` adds package provenance and permits either invoice or opening
provenance on an open item. Existing invoice data is unchanged. Downgrade refuses to
discard populated detail, including reversed historical packages.
