# ERP Phase 13.14 — Payment-specific withholding evidence intake

## Implemented

An immutable PostgreSQL evidence registry stores the
claimed source document reference, stated issuer, SHA-256
digest reference, payment date, actual posted payroll
disbursement, statutory PIT/levy line and allocation version.

The writer requires:
- Correct company and payroll calculation provenance.
- A non-reversed, non-cancelled posted salary payment.
- PIT or military-levy source identity.
- The current effective candidate/correction amount.
- An explicit source and claimed document digest.
- Idempotency and one evidence record per allocation version.

A correction changes the effective allocation version.
Old evidence stays historically recorded but cannot prove
the newer version.

## What is NOT verified

The system does not retrieve and authenticate the
document bytes, verify their SHA-256 digest, confirm the
issuer's authority, or independently verify that the
tax was actually withheld on the date of payment.

The stored digest is supplied by the caller. It is not
an independently verified cryptographic assertion.

All evidence has immutable status `unverified`.

This table must not be connected directly to an official
4DF reporting generator as proof of withholding.

## Still outstanding

1. Actual document ingestion and integrity verification.
2. Independent issuer/provenance review and approvals.
3. Full payment-specific tax reconciliation and dated
   normative 4DF gross-paid acceptance.
4. Regulatory D6 evidence and Phase 13.15 acceptance.
5. PostgreSQL runtime role separation and production
   migration/application review.

OFFICIAL GENERATION = BLOCKED.
PHASE 13 = OPEN.
