# 10.2 — VAT settings and explicit tax categories (backend V1)

Date: 2026-09-16. Migration: `e2a9c5d7f046`, from `d1f8b4c6e935`.

## Behavior

Company VAT policies and counterparty VAT registrations are append-only through
services/API, effective from a business date until the next record. Confirmation
of purchase/sales orders and invoices selects the applicable records and saves
both references on the document. Later policy changes cannot reinterpret this
snapshot. The company row is locked/refreshed during confirmation and settings
writes so concurrent policy activation cannot bypass validation through stale ORM
state. This serializes confirmations within a company; revisit lock granularity
only with a tested equivalent concurrency contract.

- A record needs payer/non-payer status, registration number for a payer, and
  legal/evidence basis. Unknown status is not inferred from the existing INN field.
- Exact replay at the same date returns the existing row; changed terms conflict.
- New dates must increase and must be later than any previously confirmed affected
  document (including subsequently cancelled documents whose confirmation remains).
- No update/delete/disable endpoint exists. Correct future settings by appending a
  new version. Correction of an erroneous historical setting needs a separately
  reviewed repair process, not silent mutation.
- Authorization: company settings use `companies.read/update`; counterparty
  histories use `counterparties.read/update`. Composite FKs enforce tenant scope.

## Migration / activation

Existing companies stay `legacy_unconfigured`; migration does not infer or backfill
registration evidence. This compatibility state retains the existing VAT behavior.
The settings GET and company responses expose it explicitly. Appending the first
company policy enables strict validation irreversibly through the supported API.
New companies created through the company API start strict, with confirmation
blocked until company and counterparty records are configured. Internal ORM/SQL
creation remains a migration-level operation and defaults to legacy.

Before activation, enter the relevant counterparty histories and a company policy
starting after previously confirmed documents. A draft dated before the first
policy cannot be confirmed in strict mode. This is a controlled forward migration,
not reconstruction of tax registration for the historical ledger. Operators must
choose the activation date from real evidence; the system does not auto-enable
existing companies or rewrite old documents.

## Rates, categories and legal basis

| Code / reason | Meaning | Strict-mode requirements |
|---|---|---|
| `VAT20` | Taxable, 20% | Effective company/seller status; complete price/method configuration |
| `VAT7`, `VAT14` | Taxable, special rate | Above plus an operation-specific `tax_legal_basis` |
| `VAT0` | Taxable, zero-rated | Explicit legal basis; distinct from exemption |
| `VAT_EXEMPT` | Exempt | Legal basis; `manual` method; no recognition event produced |
| `VAT_OUT_OF_SCOPE` | Outside VAT scope | Legal basis; `manual` method; no recognition event produced |
| `no_vat_reason=non_vat_payer` | Seller is not VAT-registered | Legal basis; all three tax configuration fields absent |

The seller is the company for sales and the supplier for purchases. A registered
buyer may buy from a non-payer supplier without creating input VAT credit.
Positive VAT on purchases by a non-payer buyer is explicitly blocked in strict
mode pending a gross-cost accounting workflow; the module does not silently
convert it to deductible input VAT. Zero/non-taxable purchase categories do not
create a positive credit.

`CASH_METHOD` additionally requires `allow_cash_method=true` in the company policy
and an operation-specific legal basis. Arbitrary manual recognition of taxable
lines is rejected in strict mode. `manual` for exempt/out-of-scope is only a
configuration marker: those lines do not create a TaxCalculation or future
recognition workload. Their category and basis remain on the confirmed line.

Authorized users supply the legal basis and verified registration evidence. These
checks enforce explicit classification and prerequisites; they do not automatically
validate all product codes, exemption certificates or the factual applicability
of a statute. This is not an online tax-registry lookup. Special tax scenarios
listed in 10.1 still require their own implementation/acceptance.

The generic rate catalog now accepts an optional inclusive `effective_until`.
The latest version is selected first; if it has expired, an older version is not
revived. Nonfinite rates and reversed date ranges are rejected. VAT20/7/14/0 keep
their existing start dates; the new category codes are classifications, not proof
that every operation has been exempt/out-of-scope since that date.

Amounts retain the existing currency rounding: exclusive prices round base then
VAT; inclusive prices round gross/base and derive VAT by subtraction, preserving
`gross = base + VAT`. Rate selection still uses the commercial document date.
Selection against actual first-event dates and overlap correction belong to 10.3.

## API

All paths begin `/api/v1/companies/{company_id}/vat-settings`:

- `GET /`: strict/legacy mode and rate/category catalog.
- `GET /policies`: policy history, `after_id` and `limit` pagination.
- `POST /policies`: append company policy.
- `GET /counterparties/{counterparty_id}/registrations`: registration history.
- `POST /counterparties/{counterparty_id}/registrations`: append registration.

Example company policy (the identifier below is illustrative):

```json
{
  "effective_from": "2026-10-01",
  "payer_status": "vat_payer",
  "vat_number": "123456789012",
  "legal_basis": "Verified registration extract, reference and date",
  "allow_cash_method": false
}
```

Counterparty registration uses the same fields except `allow_cash_method`.
An exemption line in the existing trade-document API:

```json
{
  "product_id": 1,
  "quantity": "2",
  "unit_price": "100.00",
  "tax_rate_code": "VAT_EXEMPT",
  "tax_recognition_method": "manual",
  "tax_price_mode": "exclusive",
  "tax_legal_basis": "Applicable provision and supporting document reference"
}
```

The example basis must be replaced by the actual verified basis; it is not a tax
recommendation. Ordinary draft creation permits incomplete configuration for editing;
strict validation runs again at confirmation, including service calls without HTTP.
Tax fields are copied on both draft create and update. Reloading after PATCH and
locking an invoice for confirmation refreshes the ORM state to avoid stale line
collections. Responses expose
`vat_policy_id` and `counterparty_vat_registration_id`.

## Sources and boundary of legal verification

Checked 2026-09-16: [Tax Code, section V on the official STS site](https://tax.gov.ua/nk/rozdil-v--podatok-na-dodanu-vartist/)
identifies distinct rates/categories and operation-dependent cash-method rules.
[STS cash-method explanation](https://zir.tax.gov.ua/main/bz/view/?id=39401&src=ques)
also ties this method to qualifying transactions. Engineering decision: explicit
basis plus authorized configuration, not blanket cash-method eligibility.
The [10.1 audit](phase10_1_audit.md) records source-access limits and remaining legal
verification work; this checkpoint does not certify all Ukrainian tax regimes.

## Verification / next stage

See [10.2 closure](phase10_2_closure.md). The confirmed P10-01 overlap defect remains
open and is reproducible with `python -m scripts.audit_phase10_first_event`.
10.2 changes settings/classification; 10.3 must fix first-event matching before
accepting affected VAT balances. Full block 10 is not complete.
