# Supplier quotation comparison V1

The user-selected recommendation criterion is the **lowest total payable,
including VAT and delivery**, among eligible offers. Equal totals prefer earlier
expected delivery, then quote ID for a deterministic tie. Payment terms are
shown separately and do not silently change the ranking.

## Scope and amounts

Compare one product and requested quantity, in the product's base unit, in UAH.
Enter both net and gross unit prices and a fixed delivery charge per requested
order. The system uses the supplier's quoted amounts; it does not infer VAT
rates, deductibility, or currency conversion. A non-VAT supplier can quote equal
net and gross amounts. Product identity and base-unit meaning must be consistent
when entering offers. Basket optimization and foreign-currency comparison are
outside this version.

`total_payable = round(quantity * unit_price_gross, 2) + delivery_gross`.
`total_net = round(quantity * unit_price_net, 2) + delivery_net`.
The displayed VAT difference is `total_payable - total_net`.

An offer is eligible only when the supplier is active and has type `supplier`
or `both`, any referenced contract is active/valid for purchase in this company,
the offer is active and valid on the requested order date, the quantity fits its
range, currency matches, and expected delivery meets the optional deadline.
Each rejected offer has explicit reasons. If none qualify, the recommendation
is null; no ineligible offer is selected as a fallback.

## API

Prefix: `/api/v1/companies/{company_id}/purchase-quotes`.

| Method/path | Permission | Effect |
|---|---|---|
| POST | `trade_documents.create` | Save immutable offer terms |
| GET | `trade_documents.read` | Read history, optionally filter product; `after_id`/`limit` pagination |
| POST `/compare` | `trade_documents.read` | Rank offers; no business writes |
| POST `/{quote_id}/withdraw` | `trade_documents.update` | Withdraw without deleting historical terms |

Example offer:

```json
{
  "supplier_id": 12,
  "product_id": 34,
  "reference": "SUPPLIER-2026-09-v1",
  "currency_code": "UAH",
  "min_quantity": "1",
  "max_quantity": "100",
  "unit_price_net": "100.00",
  "unit_price_gross": "120.00",
  "delivery_net": "250.00",
  "delivery_gross": "300.00",
  "lead_time_days": 3,
  "payment_term_days": 14,
  "valid_from": "2026-09-16",
  "valid_until": "2026-09-30"
}
```

Example comparison:

```json
{
  "product_id": 34,
  "quantity": "10",
  "order_date": "2026-09-16",
  "required_delivery_date": "2026-09-20",
  "currency_code": "UAH"
}
```

Offers are entered through the API; there is no automatic collection from
supplier websites or email. Comparison returns a recommendation snapshot, not a
purchase order, purchase commitment, or reservation. The user reviews the offer
and creates the purchase document with agreed prices and terms. Supplier and
contract eligibility are checked again when a purchase document is confirmed.

Reference is unique per company/supplier/product. Repeating the same reference
and terms returns its existing row; changed terms require a new reference.
Repeating a withdrawn offer does not reactivate it. Writes belong to the caller's
transaction; API writes commit once, and errors roll back.

## Purchase-document policy

Purchase documents use agreed manual prices and never inherit sales price types.
Explicit sales price types are rejected on purchase lines. Existing price and
discount snapshots remain supported.

At creation, payment terms use: explicit document value (including zero), then
contract default, then supplier default. When changing supplier/contract in a
draft without an explicit term, defaults are resolved for the new references.
Ordinary draft edits preserve the existing term snapshot. Changing a sales draft
to purchase requires explicit replacement purchase lines/prices.

Migration: `d1f8b4c6e935`, following `c0e7a3b5d824`. Composite database foreign keys
protect supplier/product/contract company identity. No dependency changes.
