# Phase 13 — D5 empty-period inventory review, 2026-11-17

Baseline: `6656ca9f6f68c24a63308e427bb05d910e47c7a2`.
Bounded functional delivery only; Phase 13 and Statutory Reporting Completion
remain OPEN. Final 13.15 acceptance was not started.

## Implemented contract

`build_d5_period_coverage_review` now accepts an empty list or tuple of targets
only when the live PostgreSQL inventory contains no active, non-reversed hire,
transfer or termination events in the company payroll period (inclusive dates).
The separate `D5_EMPTY_PERIOD_COVERAGE_REVIEW_V1` schema records that precise
scope, zero events/targets/official rows and `legal_non_applicability_confirmed=false`.
The bridge is explicitly null: no D5/D6 financial or professional review is
fabricated for an empty inventory. The populated-period V1 bridge remains intact.

Verification reconstructs the review from PostgreSQL and compares the complete
envelope and canonical digest. Rehashing a fabricated payload does not establish
provenance. Period and event reads refresh ORM state, preventing cached source
objects from hiding persisted values. Missing/foreign-company periods, invalid
or extra targets and newly appearing in-period sources fail closed.

This is read-only review evidence, not a persisted/immutable attestation or legal
non-applicability certificate. It reflects sources visible in the caller's database
transaction; it does not lock out concurrent changes or override transaction
isolation. Callers must reverify when using a review. No API/UI filing workflow,
migration, mapping approval, official generator or production rollout was added.
The supported event inventory is not every legally reportable D5 event class.

## Verification and preservation

- Combined gate: **227/227 PASS**, 0 failures, 0 errors, 0 skipped.
- New real PostgreSQL scenarios: **30**; no mocked inventory/provenance.
- Previous statutory gate: 179 cases retained; 18 existing D5 regressions added.
- Positive: empty review/replay, empty tuple, no pending writes, reversed/outside
  events, other-company events excluded, existing populated-period bridge.
- Negative: all three supported event types at both inclusive date boundaries,
  changed persisted dates behind cached period objects, cross-company/missing
  periods, malformed/extra targets, forged bridge and 12 rehashed payload changes
  including boolean-versus-integer confusion and false normative approval.
- Clone Alembic upgrade/schema check and bank-batch migration round trip: PASS.
- Source Alembic revision unchanged: PASS; disposable database removed: PASS.
- Production full logical dump SHA-256 before/after:
  `3a37fbbac88d48308d0a9ea8944db632424bac483e9ec4f3faf8b5c2c9de429c`.
- Pre-existing unrelated files: **119/119 preserved** by SHA-256 comparison.
- Existing local normative structural audit: PASS, no normative approval granted.
- Test output: 1,343 warnings; passing tests do not imply warning-free code.
- Evidence directory:
  `/var/folders/yf/wk8rhg8j5h7f_9d7ds2mwz680000gp/T/erp_phase13_statutory_gate_h2i_yhhw`
  (`summary.json`, `tests.xml`, `tests.log`, `protected.json`, `normative_audit.json`).

Reproduce from the preserved working tree:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m scripts.run_phase13_d5_empty_gate
```

The gate reuses the previous statutory gate with a delivery-specific file scope
and an expanded test list. Results include pre-existing uncommitted readiness and
source-audit material, preserved unchanged; they are not clean-checkout evidence.

## Remaining completion work

This closes the implementation limitation for empty **supported HR inventories**.
It does not close legal empty-period applicability, full statutory event coverage,
accepted-row corrections, field mapping approval or independent normative review.

D1: effective-dated categories, base/cap reconciliation and field binding,
accepted-row/correction amount evidence, independent electronic sex-code evidence.
4DF: full mixed/exceptional income category and period attribution, missing historical
payment-specific evidence, approved mappings and source accreditation.
D5: legal applicability and event coverage, accepted-row corrections and mappings.
D6: approved dated basis codes, eligibility/attestation coverage and full mappings.
Shared: header/identity binding, correction chains and requirement-to-E2E reconciliation.
See `phase13_statutory_provenance_acceptance.md` for the preceding gap audit.

`APPROVED_MAPPINGS` remains empty. No new legal-source completeness or authenticity
claim is made. Official generation stays **BLOCKED** for D1/4DF/D5/D6.
No overall completion percentage or final 13.15 acceptance is claimed.
