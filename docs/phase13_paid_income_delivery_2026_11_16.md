# Paid-income primary evidence — 2026-11-16

## Implemented

New net-entered payroll payments atomically persist gross/PIT/levy/net settlement
allocation against frozen monthly obligations, after subtracting verified posted
advances and earlier active payment allocations. The final payment consumes exact
cent residuals. Other deductions remain separate from statutory tax withholding.
Budget remittances do not supply or inflate gross income.

Immutable records have a digest, source identifiers, replayable allocation inputs,
and a frozen nominal/weekly-calendar-adjusted salary deadline. PostgreSQL rejects
updates/deletes. Reversal excludes a payment from live totals without deleting its
record. Company salary payday defaults to 7, accepts integer days 1–7, and is
available through existing company create/update/read APIs.

Read endpoints:
- `/companies/{company_id}/payroll-calculations/{id}/paid-income`
- `/companies/{company_id}/payroll-regulatory/4df/paid-income/{YYYY-MM}`

Both require `employees.manage`. Monthly review considers older accruals paid in
the requested month, next-month settlement of current accruals, and posted
advances before a monthly calculation exists. It reports missing primary evidence
explicitly and deduplicates advance sources. Historical partial payment shares
are never fabricated: only exact supported aggregate reconstruction is allowed.

## Verification

36 pure/source/snapshot tests and 16 isolated PostgreSQL regression tests.
PostgreSQL scenarios include actual automatic advance, partial/final disbursement,
monthly paid-income reading, repeat posting, reversals, immutable guard, separate
companies, bank/cash/opening-debt flows and insurance funding. New migration
`p13paidincome261115` applied successfully to the isolated database only.

## Not a claim of complete statutory 4DF support

- Prospective allocation policy is `PROSPECTIVE_REMAINING_NET_SHARE_V1`. It allocates
  already-calculated obligations; it does not recalculate tax rates or establish
  regulatory approval for each possible mixed income/benefit case.
- Next-month exception is implemented only for salary/supplement/minimum wage
  with a verified schedule deadline. Mixed salary/severance, vacation, sickness
  and leave compensation need classification and applicable deadline resolution.
  They are reported as incomplete when that resolution is required.
- Weekly schedule adjustment is supported; exceptional calendar changes and
  termination-specific settlement deadlines need separate source binding.
- The month review provides financial evidence, not official classified form rows.
  Existing official mappings/finalization gates remain unchanged and blocked.
- Main database migration is NOT applied. No production roles/triggers/data were
  changed. Deploy the migration through an authorized migration connection before
  deploying code that reads the new company column/table.

Primary rule for gross before employee taxes, salary next-month attribution, and
separation of tax remittances:
https://svp.tax.gov.ua/media-ark/news-ark/1039507.html
Retrieved 2026-11-15. This source does not itself approve a proportional historical
allocation or all mixed-income mappings.
