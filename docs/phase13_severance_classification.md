# Вихідна допомога: податки й фінансовий розрахунок — 22.10.2026

Financial integration is implemented and verified; acceptance evidence is recorded below.
This document does not close Phase 13.

The registry covers ordinary documented termination severance under the existing
Art. 44 source validator. Version v1 (01.01–21.10.2026) remains unchanged so
existing immutable snapshots still verify. Version v2 applies from 22.10.2026,
with the same classification reviewed against the official sources below.
There is no invented expiry date: future legislative changes require a new
reviewed dated version and separately dated tax rates. These version boundaries
are software support boundaries, not dates of legislative change.
The income date comes from the validated termination source within its payroll
period. Late awards and post-termination corrections need separate acceptance.

Evidence reviewed on 22.10.2026:

- DПС, 02.10.2026: ordinary severance PIT 18% and income code 127:
  https://zak.tax.gov.ua/media-ark/news-ark/1055752.html
- DПС, 19.06.2026: ordinary military levy 5%:
  https://vin.tax.gov.ua/media-ark/news-ark/1021953.html

- DПС, 29.09.2026: termination severance is other income, 4DF code 127:
  https://zp.tax.gov.ua/media-ark/news-ark/print-1054474.html
- DПС: CMU 1170, section I item 4 excludes termination severance from USC:
  https://rv.tax.gov.ua/media-ark/news-ark/787611.html
- Tax Code, 164.2.20 (other taxable income), 167.1 (PIT), 169.4.1
  (social benefit applies to wages), section XX subsection 10 item 16-1 (levy):
  https://www.tax.gov.ua/nk/rozdil-iv--podatok-na-dohodi-fizichnih-o/

Classification includes severance in PIT and military-levy bases and excludes
it from USC and wage-benefit eligibility. Rates remain separately dated service
inputs; this integrated path accepts ordinary PIT .18 / levy .05 and refuses
wage exemption or individual rates for severance. USC wage floors, ceilings
and employee-wide wage benefit allocation operate on ordinary income first.
This does not certify all other payroll line categories as wages.

Snapshot SHA proves integrity only. Authorization re-derives the current
immutable termination source and server policy and compares every snapshot
field. The caller cannot select policy or income date in the trusted writer.
Existing synthetic snapshots are not relabelled, deleted or overwritten.

## Financial integration

Accounting, payslips, zero monthly deduction results and bank disbursements use
the trusted classification. Unknown consumers and synthetic/caller-selected
policies still fail closed. Operational withholding is evaluated per payment;
the legacy nonzero monthly deduction route remains unsupported.

`POST /companies/{company_id}/payroll-calculations/{id}/severance-classification`
requires `employees.manage`, derives the policy and date from the source, and
idempotently persists the immutable snapshot. Then use the existing statutory,
accounting, payslip and payment endpoints. The caller cannot choose a tax policy
or change a stored snapshot. The classification writer takes the company lock.

Financial behavior:

- PIT and levy include severance; USC and wage social-benefit eligibility do not.
- The final accrual uses the documented termination date, including mid-month.
- The protected net is derived from the snapshot and actual tax result, with
  rounding reconciled to that result. It cannot be reduced by request input.
- Partial bank payments allocate protected severance first, then ordinary net.
  This is the application's explicit allocation convention, not a claim that
  legislation mandates that ordering. The statutory deduction ceiling applies
  only to the remaining eligible income in each payment.
- Earlier reservations must be posted or cancelled first. Reversals/cancellations
  follow the reverse payment order and cannot predate later reversals. The
  posting boundary rechecks protection, including direct journal posting.
- Advances offset ordinary net only. Excess advances cannot consume protected
  severance; their correction remains a separate operation.
- Tax payments use existing posted bank documents, with attribution and zero
  balances proved separately from the employee payment; no duplicate liability.

Official basis for protection: KZpP article 129 and the State Labour Service's
explanation:
https://dp.dsp.gov.ua/novyny/%D0%B2%D1%96%D0%B4%D1%80%D0%B0%D1%85%D1%83%D0%B2%D0%B0%D0%BD%D0%BD%D1%8F-%D0%B7-%D0%B7%D0%B0%D1%80%D0%BE%D0%B1%D1%96%D1%82%D0%BD%D0%BE%D1%97-%D0%BF%D0%BB%D0%B0%D1%82%D0%B8-%D0%BF%D1%80%D0%B0%D1%86/

Acceptance tests: `test_payroll_severance_settlement_postgresql.py` uses migrated
PostgreSQL with production triggers, full/partial payouts, withholding,
reversal/replacement, employee/tax balance reconciliation, advance offset and
HTTP permissions. `test_payroll_leave_final_settlement_postgresql.py` additionally
combines salary, leave compensation and severance in one payroll calculation.

This completes the financial route from an existing documented severance source
under the reviewed classification. It does not close all of Phase 13,
supply the unfinished statutory report rows, implement cash
payroll, or implement late severance awards and post-termination corrections.

## Acceptance — 22.10.2026

- Broad HR/payslip regression on an upgraded disposable PostgreSQL copy:
  **1404 passed** (685.61 seconds).
- Final targeted regression after the current-policy and error-mapping changes:
  **172 passed** (106.37 seconds). Includes October 22 full financial settlement,
  immutable historical policy selection, all severance tests, combined leave
  settlement, withholding, insert protection and migration roundtrips.
- Shared accounting posting/reversal tests: **62 passed**.
- No skipped PostgreSQL scenarios in these runs. Existing deprecation warnings
  remain (mainly `datetime.utcnow`); they are not test failures.
- Migration roundtrips use their own newly created databases, including a
  nonempty preservation sentinel. They no longer assume that earlier tests
  left the shared clone's immutable severance history empty.
- Working database upgraded **041 → 042** after a private verified backup.
  Row counts and hashes of **184 existing tables** are unchanged; Alembic drift
  check passes. Only the snapshot table/schema objects were added.
- Test clones were removed; working data was not used as the pytest target.

Reproduction commands (from repository root):

```sh
PYTHONPATH=. .venv/bin/python scripts/run_postgresql_regression_copy.py --upgrade-copy tests/hr tests/test_payroll_payslip_postgresql.py
PYTHONPATH=. .venv/bin/python scripts/run_postgresql_regression_copy.py --upgrade-copy tests/hr/test_payroll_severance*.py tests/hr/test_payroll_termination_severance*.py tests/hr/test_payroll_leave_final_settlement_postgresql.py tests/hr/test_payroll_deduction_execution_postgresql.py tests/hr/test_payroll_deduction_beneficiary_postgresql.py tests/hr/test_payroll_insert_freeze_postgresql.py tests/hr/test_payroll_withheld_schema_sequence_probe_postgresql.py
.venv/bin/python -m pytest -q tests/test_accounting_posting_validation.py tests/test_accounting_reversal_*source_override.py
```
