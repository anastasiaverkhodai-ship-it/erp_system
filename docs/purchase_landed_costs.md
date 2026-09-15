# Purchase Landed Cost V1

## Business contract

Landed Cost capitalizes an **already posted expense debit** in UAH. It does not
create another payable or VAT event. The source journal line, request key,
purchase order, receipt and fulfillment-line allocations remain traceable.

The current allocation basis is receipt quantity. Every receipt-line allocation
must be positive and the allocated amounts must equal the source amount exactly.
Requests too small to give every receipt line a positive monetary allocation
are rejected. Weights, customs values, multiple currencies and new supplier
invoices are outside this V1 API.

Non-refundable delivery/customs costs for goods returned to the supplier return
to the **original expense account**. They are not redistributed to other stock.
The purchase-return physical source must be recorded as a TradeReturnEvent.
Generic non-expense issues without explicit transfer/return provenance fail.

## API

Under `/api/v1/companies/{company_id}/purchase-landed-costs`:

- `GET /`: source/reversal history (`journal_entries.read`).
- `POST /`: capitalize (`journal_entries.post`).
- `POST /{event_id}/reverse`: reverse capitalization (`journal_entries.reverse`).

Create example:

```json
{
  "source_journal_entry_line_id": 123,
  "request_key": "delivery-invoice-456-receipt-789",
  "trade_document_id": 45,
  "warehouse_document_id": 789,
  "amount": "100.00",
  "cost_date": "2026-09-15"
}
```

`request_key` is unique within the company. Identical retries reuse the original
result; changed payloads are rejected. Reusing a key after reversal also returns
the original request, so a genuinely new capitalization needs a new key.
Company/user authorization comes from the existing API permission system.

## Inventory and accounting

Original FIFO/MA physical quantities and base costs remain unchanged by
capitalization. An additional monetary layer follows existing consumption,
transfer and sales-return provenance. Final destinations use deterministic
cent rounding that conserves the amount.

- On-hand destination: Dr inventory / Cr original expense.
- Sold/expensed destination: Dr historical issue expense / Cr original expense.
- Customer return: move the corresponding additional cost back to inventory.
- Supplier return without reimbursement: reverse that part of capitalization.
- Cancellation: reverse the exact previous journals and append reversal events.

Reconciliation runs after complete document posting/reversal, fulfillment,
warehouse transfer and return lifecycle operations. Nested operations reconcile
once. Company-row locking serializes inventory operations with capitalization;
request and source-capacity checks run inside that lock. All services leave
commit/rollback to the caller; HTTP writes commit once and roll back on failure.
Operations that change existing valuation must not predate its recognition.
Closed/missing accounting periods reject writes.

The valuation-event `journal_entry_id` is the exact accounting link. Direct
reversal of managed journals or an expense with active capitalization is blocked.
Receipt reversal is blocked while its landed-cost source remains active.
Sales gross-profitability includes net issued landed costs after returns.

The base FIFO/MA records alone are **not the total inventory value**. Consumers
must combine them with the signed landed-cost valuation events. On-hand monetary
adjustments retain product/warehouse/lot identity. Supplier-return portions are
no longer capitalized; the original source amount remains immutable.

## Database and verification

Migrations:

1. `b9d6f2a4c713`: source/allocation foundation; corrected PostgreSQL FK targets
   and explicit short index names.
2. `c0e7a3b5d824`: expense linkage and immutable valuation/GL provenance.

Validation command (requires the configured development PostgreSQL database):

```sh
RUN_POSTGRES_E2E=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m alembic check
```

Dedicated tests cover original/duplicate/reversal requests, expense capacity,
concurrent requests, real posting, FIFO/MA transfers and their reversal,
operational customer returns and cancellation, supplier-return expense policy,
closed-period rollback, protected journals, API authentication, profitability,
and migration upgrade/downgrade/re-upgrade. New fixtures use private schemas;
concurrency fixtures explicitly remove their private schemas in `finally`.

The existing full PostgreSQL suite also covers purchase recognition, payments,
advances, returns, purchase value corrections, VAT adjustment integration,
sales and warehouse operations. Legacy datetime/TestClient deprecation warnings
remain; dependencies are unchanged.

This document records the scope of the Landed Cost checkpoint. It does not
certify completion of every item in the overall ERP roadmap or add a frontend.
