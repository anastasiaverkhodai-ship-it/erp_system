# Phase 13 — R12-H Independent Source Acceptance

**Assessment date:** 2026-11-09

**Source baseline:** `d9f3116`

**Code Alembic head:** `p13receiptread261109`

**Production Alembic:** `p13d6grant261105`

## 1. Verified technical controls

- Source files parsed: **8/8 PASS**
- Static source-contract checks: **11/11 PASS**
- Receipt security/unit regression: **84/84 PASS**
- Existing PostgreSQL regression: **80/80 PASS**
- PostgreSQL regression skips: **0**
- JWT and dedicated PostgreSQL integration: previously passed
  against a disposable clone; production rollout not approved

These checks establish the presence of technical controls and
regression behavior. Static checks do not independently establish
external document authenticity.

## 2. External evidence acceptance

| Requirement | Acceptance |
|---|---|
| Independent issuer accreditation | NOT PROVIDED |
| Authentic issuer identity confirmed outside the ERP | NOT VERIFIED |
| External proof of actual tax withholding | NOT PROVIDED |
| Independent payment-to-withholding reconciliation | NOT APPROVED |
| Source-bound signed receipt controls | TECHNICALLY TESTED |
| Replay and mismatch protection | TECHNICALLY TESTED |
| Production reader credentials | NOT PROVISIONED |
| Production public API | BLOCKED |
| Official statutory generation | BLOCKED |

The absence of provided evidence in this assessment is **not**
proof that no external evidence exists elsewhere.

## 3. Required independent evidence

1. Authoritative issuer identity and accreditation evidence,
   independently checked against an approved source.
2. An authoritative external record confirming actual tax
   withholding/payment, not merely a client-provided receipt.
3. Reconciliation of that record to the correct company,
   payroll payment, tax component, amount and effective date.
4. Independent review, reproducible source references,
   correction history and negative mismatch/replay tests.
5. Controlled operational deployment of the dedicated reader
   role, secrets and API only after security acceptance.

## 4. Release decision

**R12-H = OPEN**

**ISSUER IDENTITY = NOT_VERIFIED**

**ACTUAL WITHHOLDING = NOT_VERIFIED**

**OFFICIAL GENERATION = BLOCKED**

**PHASE 13 = OPEN**

No automatic approval, regulatory submission or production
migration is authorized by this report.

## 5. Acceptance boundary

The existing regression covers many statutory workflows,
but it does not by itself establish independent external
issuer authenticity or tax withholding. No synthetic
receipt, configured public key or internal payroll record
may be treated as conclusive external proof.
