# ERP Phase 13.14 — 4DF Payment + D6 Basis Evidence

## Implemented

- Canonical source-bound payment allocation inventory
- Unique disbursement and tax remittance references
- Payroll calculation and employee scope checks
- Real PostgreSQL posted-payment verification integration
- Payment dates and monetary precision validation
- Deterministic manifest fingerprints
- D6 candidate code syntax, period and document-reference checks
- Explicit non-approval for all candidate D6 mappings

## Financial limitations

A payroll tax remittance is not automatically withholding
allocated to a particular net salary payment.

The module verifies posted payments and remittances
through the existing PostgreSQL-backed service.

It deliberately does not compute official 4DF gross paid.
Payment-specific withholding and correct reporting-month
attribution remain separate acceptance requirements.

## D6 limitations

The code must consist of eight digits. This is only
syntax verification, not approval of its meaning.

The D6 inventory does not yet independently join each
candidate to live HR employment/service-period sources
or an approved effective-dated official basis-code registry.

The actual mapping and source binding remain open.

## Regulatory sources

https://zakon.rada.gov.ua/laws/show/z0751-26
https://zakon.rada.gov.ua/laws/show/z0111-15

No official statutory rows are generated.

OFFICIAL GENERATION = BLOCKED.
PHASE 13 = OPEN.
