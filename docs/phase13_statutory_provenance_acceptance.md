# Phase 13 — statutory provenance delivery, 2026-11-16

Baseline: `13dc734bf52567893af646f91d1b1a55b169f187` on local main and remote main.
This is a bounded implementation and verification delivery, not completion of
Statutory Reports & Remaining Gaps and not final Phase 13.15 acceptance.

## Implemented and exercised

Independent database verification now precedes persistence of internal source
snapshots for D1/4DF/D5/D6, reviewed D1/4DF rows and dated 4DF rows. Arithmetic
consistency and a digest of caller/projected data do not establish provenance.
The verifier reloads current calculations, identities, frozen totals and component
lines instead of accepting stale SQLAlchemy identity-map objects. Existing report
status and replay rows are refreshed under the existing company/report locks.
A cached draft or cached payload cannot conceal a database change.

The new PostgreSQL tests use real payroll/statutory sources. Positive scenarios
cover all four snapshot kinds and exact replay. Negative scenarios cover stale
identity, changed gross/net with preserved arithmetic, company/period mismatch,
duplicate and missing calculations, component substitution, modified persisted
payloads, and cached draft status. Rejections occur before new row insertion.
Official generation is rejected for all four forms; snapshots remain drafts.

Two older persistence-only tests deliberately stub synthetic provenance, matching
their existing mocked readiness/payment scope. They are not counted as independent
source verification or official mapping approval. The new provenance tests and
the existing real 4DF payment E2E exercise the actual verifier.

No new migration, approved mapping, official generator, production rollout, or
synthetic historical payment evidence is introduced.

## Normative audit and current blockers

The pre-existing, locally modified `scripts/audit_payroll_reference_sources.py`
was executed unchanged. It checks 9,102 profession pairs in each older workbook
appendix, 158 income rows/112 codes, six corroborated malformed codes, and zero
income-interval overlaps after documented resolutions. It returns
`production_approved=false`. These figures describe that audit's source set,
not the newer admitted civil-profession registry.

The 13 November civil registry admission supersedes the earlier blanket claim
that all D5 profession sources are unapproved: see
`normative_evidence/2026_11_13/APPROVAL.md` and
`phase13_d5_supported_period.md`. Admission covers profession identities and
supported dates from 2026-01-01; it does not approve statutory form mappings.

External checks in this run:
- DPS electronic form registry was accessible:
  https://www.tax.gov.ua/data/material/000/006/58768/Forms_deklar.htm
- The DPS explanation of 4DF columns 3a/3 was accessible:
  https://svp.tax.gov.ua/media-ark/news-ark/1039507.html
- https://zakon.rada.gov.ua/laws/show/z0751-26 timed out. No new completeness or
  authenticity approval is inferred from this failed retrieval.

| Area | Verified technical capability | Still open |
|---|---|---|
| D1 | Internal source snapshots, field/XML validation, positive source-controlled financial review | Complete effective-dated categories, base/cap reconciliation and ERP field binding; accepted-row amount invariance/correction delta evidence; independent electronic sex-code confirmation |
| 4DF | Frozen paid-income evidence and real split-payment review; strengthened source provenance | Complete income-category and period attribution for mixed/exceptional cases; historical payment-specific evidence where absent; approved mapping and independent source accreditation |
| D5 | Admitted civil profession registry for supported dates; HR candidate/field controls | Full statutory event/applicability coverage, accepted-row correction evidence and field mapping approval. The existing period-coverage service still explicitly rejects empty periods; this is an implementation limitation, not proof of non-applicability |
| D6 | HR/contract scope binding, applicability evidence controls and XML structure checks | Independently approved dated basis codes, legal eligibility/attestation coverage and full statutory mapping |
| Shared | Live financial-source verification before persistence | Complete header/identity field binding; supported correction chains; independent normative approval for every required mapping |

`APPROVED_MAPPINGS` remains empty. Structural XSD validation, passing tests,
classifier admission and financial evidence never grant filing permission.
Do not treat the generic payroll source snapshot as a complete D5 HR-event or D6
special-service inventory.

## Preservation and verification

- Final focused run: **179/179 PASS**, 0 failures, 0 errors, 0 skipped.
- New real PostgreSQL provenance cases: 26.
- Clone upgrade, Alembic schema check and bank-batch migration round trip: PASS.
- Disposable database removed: PASS (see tests.log).
- Pre-existing unrelated files: **119/119 preserved**, exact SHA-256 comparison.
- Production full logical dump before/after: identical, SHA-256
  `3a37fbbac88d48308d0a9ea8944db632424bac483e9ec4f3faf8b5c2c9de429c`.
- Production Alembic revision: `p13d6grant261105`, unchanged.
- Normative structural audit: PASS; no new statutory approval.
- Prior expanded run: 177 passed / 2 failed due to missing generation metadata
  in two new fixtures. Fixtures corrected; final run above supersedes that result.
- Evidence: `/var/folders/yf/wk8rhg8j5h7f_9d7ds2mwz680000gp/T/erp_phase13_statutory_gate_nitxkom6`
  (summary.json, tests.xml, tests.log, protected.json, normative_audit.json).
- No full Phase 13.15 regression claimed.

Reproduce the focused gate:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m scripts.run_phase13_statutory_gate
```

The gate executes the unchanged local normative audit, hashes pre-existing dirty
files outside its explicit scope, fingerprints the production logical dump before
and after, upgrades/checks only a disposable PostgreSQL clone, and rejects failed
or skipped tests. Its output includes `full_stage_acceptance=false`.
The evidence reflects the preserved working tree, including existing uncommitted
readiness/audit changes; it is not a claim that a clean checkout contains those
uncommitted source acquisitions or edits.

## Remaining work and release decision

The historical acceptance baseline and consolidated gap matrix are not an
up-to-date proof that every old risk is still a defect. Conversely, these focused
checks do not establish closure of all Phase 13.1–13.13 requirements. A complete
requirement-to-E2E reconciliation remains outstanding; no percentage of total
functional completion is asserted.

- Bank Batch Completion: previously accepted.
- Statutory Reports & Remaining Gaps: OPEN; this delivery closes the identified
  persistence/provenance defects only.
- Final Phase 13.15 acceptance: reserved for a separate run; not performed here.
- Official statutory generation: BLOCKED for D1, 4DF, D5 and D6.
- Phase 13: OPEN.
