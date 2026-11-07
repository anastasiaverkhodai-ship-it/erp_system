# ERP 13.14 — Source-bound withholding reconciliation

## Confirmed database separation

`payroll_statutory_result_lines` contains accrued
PIT, military levy and employer USC components.

`payroll_disbursements` contains payment net and
`withheld_amount` used by non-statutory deduction
execution, not independently itemized PIT/levy.

`payroll_withholding_batches` and
`payroll_withholding_lines` belong to operational
non-statutory/enforcement deductions.

`payroll_tax_remittance_lines` records payments to the
budget by calculation, not tax actually withheld from
each separate net salary payment.

## Implemented review

- Real source-bound statutory PIT/levy reconciliation
- Active posted payment verification
- Partial payment inventory by actual date
- Non-tax withholding separation
- Overpayment and duplicate source blocking
- Cross-company source isolation
- Immutable-style deterministic SHA-256 review fingerprint
- Explicit no-approval of 4DF gross paid

The reported remaining net is before other settlements,
advances, refunds and correction chains. It must not
be treated as final outstanding liability.

## Still required for complete payment-specific attribution

Create and approve an independent payment-specific
tax-withholding evidence source, including dated PIT and
levy components, source journal linkage, correction and
reversal semantics, concurrency and anti-duplication
controls, and actual payroll write-path integration.

Only then can 4DF gross paid attribution and reporting
month assignment be considered for regulatory acceptance.

Official generation remains BLOCKED.
Phase 13 remains OPEN.
