# ERP Phase 13 — Consolidated Final Gap Matrix

**Assessment:** 2026-11-09

**Baseline commit:** `c1de652`

**Code Alembic:** `p13receiptread261109`

**Production Alembic:** `p13d6grant261105`

## Scope and evidence standard

This matrix inventories existing Phase 13 documents. It does not independently validate claims made by those documents and does not constitute final Phase 13 acceptance.

Documentary PASS statements are not treated as proof that every related business requirement has been completed.

## Acceptance document inventory

| Document | Explicit risk signals | Other status signals |
|---|---:|---:|
| `docs/phase13_14_internal_reconciliation_contract.md` | 1 | 2 |
| `docs/phase13_2026_normative_controls.md` | 3 | 3 |
| `docs/phase13_2_closure.md` | 0 | 0 |
| `docs/phase13_4df_combined_source_binding.md` | 3 | 1 |
| `docs/phase13_4df_d6_acceptance_review.md` | 2 | 1 |
| `docs/phase13_4df_d6_evidence_gate.md` | 3 | 0 |
| `docs/phase13_4df_independent_provenance.md` | 4 | 0 |
| `docs/phase13_c3_aggregate_safety.md` | 7 | 8 |
| `docs/phase13_closure_worklog.md` | 8 | 1 |
| `docs/phase13_combined_d1_4df_d6_financial_review.md` | 5 | 1 |
| `docs/phase13_completion_plan.md` | 5 | 3 |
| `docs/phase13_custody_role_separation.md` | 2 | 1 |
| `docs/phase13_d5_classifier_source_review.md` | 2 | 3 |
| `docs/phase13_d5_database_category_transition.md` | 3 | 0 |
| `docs/phase13_d5_database_correction_chain.md` | 5 | 1 |
| `docs/phase13_d5_database_field_projection.md` | 2 | 0 |
| `docs/phase13_d5_event_field_candidate.md` | 3 | 1 |
| `docs/phase13_d5_field_definition_review.md` | 3 | 0 |
| `docs/phase13_d5_field_rule_content_review.md` | 3 | 1 |
| `docs/phase13_d5_full_extraction_acceptance.md` | 3 | 1 |
| `docs/phase13_d5_normative_acceptance_register.md` | 4 | 3 |
| `docs/phase13_d5_period_coverage.md` | 4 | 2 |
| `docs/phase13_d5_regulatory_review_bridge.md` | 2 | 1 |
| `docs/phase13_d5_scoped_field_evidence.md` | 4 | 0 |
| `docs/phase13_d5_source_admission_acceptance.md` | 5 | 3 |
| `docs/phase13_d5_verified_review_snapshot.md` | 3 | 2 |
| `docs/phase13_d6_live_hr_binding_and_4df_withholding_gap.md` | 3 | 0 |
| `docs/phase13_db_role_separation_plan.md` | 1 | 1 |
| `docs/phase13_deduction_payment_execution.md` | 0 | 0 |
| `docs/phase13_final_acceptance.md` | 0 | 0 |
| `docs/phase13_financial_acceptance_2026_11_04.md` | 0 | 0 |
| `docs/phase13_leave_correction_acceptance.md` | 0 | 0 |
| `docs/phase13_leave_correction_workflow.md` | 0 | 0 |
| `docs/phase13_leave_termination_acceptance.md` | 0 | 1 |
| `docs/phase13_normative_content_2026_11_05.md` | 0 | 0 |
| `docs/phase13_payment_specific_withholding_evidence.md` | 3 | 1 |
| `docs/phase13_payment_tax_allocation_candidates.md` | 4 | 1 |
| `docs/phase13_payment_tax_allocation_corrections.md` | 2 | 0 |
| `docs/phase13_payment_tax_allocation_review_reversal.md` | 3 | 1 |
| `docs/phase13_payment_tax_document_integrity.md` | 5 | 0 |
| `docs/phase13_payment_tax_independent_source_audit.md` | 3 | 1 |
| `docs/phase13_payment_tax_secure_custody.md` | 2 | 0 |
| `docs/phase13_postgresql_role_separation_proof.md` | 2 | 0 |
| `docs/phase13_r12h_independent_source_acceptance.md` | 13 | 5 |
| `docs/phase13_reference_review_2026_11_06.md` | 5 | 5 |
| `docs/phase13_regulatory_delivery_2026_11_04.md` | 0 | 0 |
| `docs/phase13_rules_matrix.md` | 0 | 0 |
| `docs/phase13_severance_classification.md` | 2 | 2 |
| `docs/phase13_sick_correction_acceptance.md` | 0 | 0 |
| `docs/phase13_three_processes_acceptance.md` | 0 | 0 |
| `docs/phase13_withholding_source_reconciliation.md` | 3 | 1 |

## Explicit risk statements by source

Each reference identifies a line in a source document. A risk signal is a review candidate, not automatically an unresolved functional defect.

### `docs/phase13_14_internal_reconciliation_contract.md`

- L37: Official generation must remain blocked until exact current

### `docs/phase13_2026_normative_controls.md`

- L51: ## Open acceptance
- L60: OFFICIAL GENERATION = BLOCKED.
- L61: PHASE 13 = OPEN.

### `docs/phase13_4df_combined_source_binding.md`

- L19: NOT_VERIFIED.
- L36: All official form generation remains BLOCKED.
- L38: Phase 13 is OPEN.

### `docs/phase13_4df_d6_acceptance_review.md`

- L11: - Explicit review-only and blocked statutory-generation controls
- L26: All official generation remains BLOCKED.

### `docs/phase13_4df_d6_evidence_gate.md`

- L35: The actual mapping and source binding remain open.
- L44: OFFICIAL GENERATION = BLOCKED.
- L45: PHASE 13 = OPEN.

### `docs/phase13_4df_independent_provenance.md`

- L26: NOT VERIFIED. Source fingerprint verification alone
- L40: not approved. No official rows are generated.
- L45: OFFICIAL STATUTORY GENERATION = BLOCKED.
- L46: PHASE 13 = OPEN.

### `docs/phase13_c3_aggregate_safety.md`

- L27: - Missing instructions, company/contract mismatches, currency mismatches or
- L29: - Missing, unverified or invalid evidence, including on a later item.
- L42: ten test modules; no skipped or failed tests.
- L73: | Milestone | Remaining gates |
- L91: Review found and fixed two issues in the pending C3 package:
- L94: cached pending instance from overwriting a completed verification. A second
- L106: finished with **4085 passed, 1 failed**, 8205 warnings in 1218.14 seconds.

### `docs/phase13_closure_worklog.md`

- L54: - Вихідна зарплатна регресія: 174 passed / 1 failed. Старий тест пільги
- L60: - Перший прогін сукупної бази та неповного місяця: 26 passed / 2 failed;
- L73: Регресія решти ERP: 3647 passed / 1 failed. Падіння розрахункового листка
- L214: Широка регресія решти ERP: 3647 passed, 1 failed (18 хв 44 с). Падіння
- L243: Перевірки: спочатку 9 passed / 1 failed (часовий пояс відповіді API), після
- L302: Перша перевірка: 6 passed / 1 failed — очікування тексту помилки для відсутньої
- L338: separate fail-closed aggregate assessment. Mixed categories, unverified evidence,
- L345: Full ERP regression: **4085 passed / 1 failed** (20m18s); the old year-end

### `docs/phase13_combined_d1_4df_d6_financial_review.md`

- L27: When 4DF groups are absent, the result is NOT_VERIFIED.
- L31: D6 may be COMPLETE, INCOMPLETE or MIGRATION_REQUIRED.
- L32: Its normative mapping remains NOT_APPROVED, including
- L41: OFFICIAL STATUTORY GENERATION = BLOCKED.
- L42: PHASE 13 = OPEN.

### `docs/phase13_completion_plan.md`

- L123: - Початкова повна регресія: 3703 passed, 1 failed. Єдине падіння — тест
- L243: 1 failed**. Старий тест відкату початкових залишків не враховував залежність
- L353: - Повний прогін `tests/hr`: **104 passed, 2 failed**. Один збій був у підготовці
- L521: - Перший цільовий прогін: 5 passed, 1 failed через неоднаковий формат
- L695: - Розширений HR-прогін: 1169 passed, 1 failed, 513,64 с.

### `docs/phase13_custody_role_separation.md`

- L41: ## Remaining blockers
- L50: Official statutory generation remains BLOCKED.

### `docs/phase13_d5_classifier_source_review.md`

- L3: Status: PENDING INDEPENDENT APPROVAL
- L31: D5 statutory readiness and report generation remain blocked.

### `docs/phase13_d5_database_category_transition.md`

- L30: Legacy HR events without those fields remain blocked.
- L39: OFFICIAL D5 GENERATION = BLOCKED.
- L40: PHASE 13 = OPEN.

### `docs/phase13_d5_database_correction_chain.md`

- L26: OFFICIAL D5 GENERATION = BLOCKED.
- L27: PHASE 13 = OPEN.
- L41: unapproved and fail closed.
- L50: as its replacement target makes the correction review fail
- L58: Official generation remains BLOCKED.

### `docs/phase13_d5_database_field_projection.md`

- L42: OFFICIAL D5 GENERATION = BLOCKED.
- L43: PHASE 13 = OPEN.

### `docs/phase13_d5_event_field_candidate.md`

- L35: 6. Implement remaining D5 categories and exceptions.
- L39: Official D5 generation remains BLOCKED.
- L40: Phase 13 remains OPEN.

### `docs/phase13_d5_field_definition_review.md`

- L16: excerpts, excerpt hashes, missing fields and duplicates.
- L32: OFFICIAL D5 GENERATION = BLOCKED.
- L33: PHASE 13 = OPEN.

### `docs/phase13_d5_field_rule_content_review.md`

- L32: ## Remaining normative acceptance
- L42: Official D5 generation remains BLOCKED.
- L43: Phase 13 remains OPEN.

### `docs/phase13_d5_full_extraction_acceptance.md`

- L26: Each extraction error or missing textual content is explicit.
- L40: Official D5 generation remains BLOCKED.
- L41: Phase 13 remains OPEN.

### `docs/phase13_d5_normative_acceptance_register.md`

- L12: - Official statutory D5 field mapping: NOT APPROVED.
- L13: - Official D5 generation: BLOCKED.
- L14: - Phase 13: OPEN.
- L38: - Keep statutory generation BLOCKED until acceptance.

### `docs/phase13_d5_period_coverage.md`

- L27: ## Remaining limitations
- L33: Empty-period review is currently blocked.
- L45: OFFICIAL D5 GENERATION = BLOCKED.
- L46: PHASE 13 = OPEN.

### `docs/phase13_d5_regulatory_review_bridge.md`

- L38: OFFICIAL D5 GENERATION = BLOCKED.
- L39: PHASE 13 = OPEN.

### `docs/phase13_d5_scoped_field_evidence.md`

- L30: ## Remaining work
- L42: All these criteria remain OPEN.
- L44: OFFICIAL D5 GENERATION = BLOCKED.
- L45: PHASE 13 = OPEN.

### `docs/phase13_d5_source_admission_acceptance.md`

- L10: - Real normative document authenticity: NOT APPROVED.
- L11: - Official D5 field mappings: NOT APPROVED.
- L12: - Official D5 generation: BLOCKED.
- L13: - Phase 13: OPEN.
- L26: ## Remaining source acceptance

### `docs/phase13_d5_verified_review_snapshot.md`

- L34: correction types, filing, and signature approval remain blocked.
- L36: OFFICIAL D5 GENERATION = BLOCKED.
- L37: PHASE 13 = OPEN.

### `docs/phase13_d6_live_hr_binding_and_4df_withholding_gap.md`

- L29: ## Remaining statutory requirements
- L36: OFFICIAL GENERATION = BLOCKED.
- L37: PHASE 13 = OPEN.

### `docs/phase13_db_role_separation_plan.md`

- L3: Status: NOT IMPLEMENTED. Gate 1 OPEN.

### `docs/phase13_payment_specific_withholding_evidence.md`

- L22: ## What is NOT verified
- L47: OFFICIAL GENERATION = BLOCKED.
- L48: PHASE 13 = OPEN.

### `docs/phase13_payment_tax_allocation_candidates.md`

- L34: ## Acceptance still open
- L42: OFFICIAL GENERATION = BLOCKED.
- L43: PHASE 13 = OPEN.
- L72: If the relation remains missing, the test prints

### `docs/phase13_payment_tax_allocation_corrections.md`

- L27: OFFICIAL GENERATION = BLOCKED.
- L28: PHASE 13 = OPEN.

### `docs/phase13_payment_tax_allocation_review_reversal.md`

- L30: ## Remaining acceptance work
- L38: Official generation remains BLOCKED.
- L39: Phase 13 remains OPEN.

### `docs/phase13_payment_tax_document_integrity.md`

- L49: Independent issuer verification: NOT VERIFIED.
- L50: Payment-specific withholding: NOT VERIFIED.
- L51: 4DF gross paid: NOT APPROVED.
- L52: Official statutory generation: BLOCKED.
- L53: Phase 13: OPEN.

### `docs/phase13_payment_tax_independent_source_audit.md`

- L25: ## Non-claims and remaining blockers
- L31: Gross paid for 4DF is NOT approved.
- L32: Official statutory generation remains BLOCKED.

### `docs/phase13_payment_tax_secure_custody.md`

- L49: Official 4DF and D6 generation stays BLOCKED.
- L50: Phase 13 stays OPEN.

### `docs/phase13_postgresql_role_separation_proof.md`

- L44: remain pending.
- L46: Official generation remains BLOCKED.

### `docs/phase13_r12h_independent_source_acceptance.md`

- L19: against a disposable clone; production rollout not approved
- L29: | Independent issuer accreditation | NOT PROVIDED |
- L30: | Authentic issuer identity confirmed outside the ERP | NOT VERIFIED |
- L31: | External proof of actual tax withholding | NOT PROVIDED |
- L32: | Independent payment-to-withholding reconciliation | NOT APPROVED |
- L35: | Production reader credentials | NOT PROVISIONED |
- L36: | Production public API | BLOCKED |
- L37: | Official statutory generation | BLOCKED |
- L57: **R12-H = OPEN**
- L59: **ISSUER IDENTITY = NOT_VERIFIED**
- L61: **ACTUAL WITHHOLDING = NOT_VERIFIED**
- L63: **OFFICIAL GENERATION = BLOCKED**
- L65: **PHASE 13 = OPEN**

### `docs/phase13_reference_review_2026_11_06.md`

- L13: annex state commencement on 2015-10-01. This removes the missing-full-scan issue
- L33: ## Remaining before approval
- L46: The missing-annex issue for amendment 5 is resolved. General commencement is
- L59: Thus the earlier missing-file statements above describe the initial review,
- L60: not the latest acquisition state. The remaining blockers are dated-history

### `docs/phase13_severance_classification.md`

- L47: policies still fail closed. Operational withholding is evaluated per payment;
- L65: only to the remaining eligible income in each payment.

### `docs/phase13_withholding_source_reconciliation.md`

- L31: The reported remaining net is before other settlements,
- L46: Official generation remains BLOCKED.
- L47: Phase 13 remains OPEN.

## Mandatory final acceptance decisions

| Area | Current acceptance decision |
|---|---|
| R12-G2 test integration | JWT/PostgreSQL clone E2E passed; production rollout not approved |
| R12-H issuer accreditation | NOT VERIFIED |
| R12-H actual withholding | NOT VERIFIED |
| R12-H independent reconciliation | NOT APPROVED |
| Production reader credentials/API | NOT PROVISIONED / BLOCKED |
| D1, 4DF, D5, D6 statutory acceptance | Requires source-by-source review of referenced reports |
| Leave / sick leave / severance / taxes / payments | Requires requirement-by-requirement reconciliation |
| Full Phase 13 final regression | NOT ESTABLISHED BY THIS REPORT |
| Official statutory generation | BLOCKED |
| Phase 13 final acceptance | OPEN |

## Required closure actions

1. Reconcile every original Phase 13.1–13.15 requirement against implementation and E2E evidence.
2. Review each documented risk signal and distinguish current blockers from historical findings.
3. Verify tenant-reader production deployment and credential isolation before enabling any API.
4. Obtain independent issuer and withholding evidence before approving R12-H.
5. Re-run final PostgreSQL E2E and full regression after resolving material gaps.
6. Record final sign-off only for verified items.

## Release decision

**R12-H = OPEN**

**OFFICIAL GENERATION = BLOCKED**

**PHASE 13 = OPEN**
