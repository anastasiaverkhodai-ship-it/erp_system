# Phase 9 — Procurement / Accounts Payable closure audit

Audit date: 2026-09-16.
Scope: backend V1, including the clarified automatic supplier-offer comparison
and the previously implemented Landed Cost V1. Phase 9 backend V1 is closed within the explicit boundaries below.

## Requirements-to-evidence matrix

| Original requirement | Implementation / evidence | Result |
|---|---|---|
| 9.1 Supplier selection / purchase terms / procurement rules | `purchase_quote_service`, `purchase_policy_service`, company-scoped quote API, purchase confirmation revalidation; comparison and real-PG tests | Implemented: rank eligible offers by gross delivered payable; validate supplier, contract, dates, quantities and delivery; inherit payment terms |
| 9.2 Purchase Order | `confirm_purchase_order`, `cancel_purchase_order`; purchase order confirmation/cancellation tests | Implemented |
| 9.3 Goods Receipt | `execute_purchase_order_fulfillment` and reversal; receipt-price bridge; FIFO/MA PG tests | Implemented |
| 9.4 Supplier Invoice | `confirm_purchase_invoice`, `cancel_purchase_invoice`, open-item creation | Implemented |
| 9.5 PO ↔ Receipt ↔ Invoice | `InvoiceFulfillmentAllocation`, `invoice_fulfillment_allocation_service`; API and reconciliation tests | Implemented |
| 9.6 Accounts Payable | `counterparty_open_item_service`, PAYABLE read API; economic-liability calculation | Implemented |
| 9.7 Supplier settlements | `payment_settlement_service`; supplier chronology PG tests | Implemented |
| 9.8 Supplier advances | Supplier advance persistence/reconciliation/GL lifecycle; PG payment-first and correction scenarios | Implemented |
| 9.9 Purchase Returns | Immutable trade-return/recognition events, recognition/reversal/VAT-adjustment integration; purchase-return PG chronology | Implemented service lifecycle |
| 9.10 Purchase value corrections | Allocation, FIFO impacts, moving-average replay, GL, VAT overlay, supplier clearing and transfer/return interactions | Implemented and regression-covered |
| 9.11 Procurement analytics foundation | PAYABLE API exposes supplier, original/settled/open amount and due date; supplier/date filters; economic liability calculation | Foundation implemented; full historical AP aging/statements remain Phase 19 |
| Landed Cost, added in the earlier Phase-9 gap audit | Source expense linkage, quantity allocation, FIFO/MA destinations, GL, transfers, returns, reversal, idempotency and concurrency tests | Implemented; first checkpoint commit `c9343fe` |

The original roadmap and earlier closure discussion explicitly assign full VAT
lifecycle to Phase 10, FX/cross-currency to Phase 18, and AP aging/statements/report
screens to Phase 19. These have not been reclassified as delivered here.

## Issues found and addressed in this audit

1. Purchase API used sales commercial defaults. Purchase lines now bypass sales
   policy and reject sales price types; explicit purchase amounts remain intact.
2. Purchase documents did not inherit payment terms from the chosen contract or
   supplier. Omitted terms now inherit; explicit zero is preserved. Confirmation
   checks the counterparty still qualifies as a supplier.
3. Automatic supplier comparison was absent. It now persists offer terms,
   reports ineligible offers and ranks by the user's chosen gross delivered
   payable criterion, with deterministic tie-breaking.
4. Direct journal reversal could bypass landed-cost inventory reconciliation.
   Journals for affected warehouse documents now require the complete inventory
   lifecycle. Real FIFO/MA tests prove normal issue reversal still succeeds and
   restores landed value without changing the supplier liability.

## Explicit V1 boundaries

- This is a backend/API milestone. User-facing screens remain Phase 22.
- Quote comparison handles one product in its base unit and UAH. It does not
  collect offers externally, optimize baskets, convert currencies or place orders.
- Recommendation is a current snapshot; final prices/terms are reviewed when
  preparing the purchase document. Purchase-document confirmation revalidates
  supplier/contract eligibility.
- Existing purchase return/value-correction services remain their canonical
  orchestration interfaces; this audit does not introduce a new UI for them.
- Landed Cost uses already posted expenses, quantity allocation and positive
  per-receipt-line allocations. See `purchase_landed_costs.md` for exact limits.
- This is not certification of all Ukrainian tax requirements or production
  readiness of the entire ERP.

## Verification

- Full repository, `RUN_POSTGRES_E2E=1`: **3058 passed, 0 failed, 0 skipped**.
- PostgreSQL and code head: **d1f8b4c6e935**.
- `alembic check`: **No new upgrade operations detected**.
- Quote migration: isolated-schema upgrade/downgrade/re-upgrade and metadata
  comparison passed; offline SQL generation passed.
- OpenAPI includes quotation create/list/compare/withdraw routes.
- No dependency changes. Existing library deprecation warnings remain (1273
  warnings in the full run); they are not test failures.
- Base checkpoint: `c9343fe`; this closure change follows it on `main`.

**Result: Phase 9 backend V1 COMPLETE. Next planned phase: Phase 10, Ukrainian VAT.**
