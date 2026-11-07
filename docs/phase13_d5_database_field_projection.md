# ERP 13.14 — Database-Bound D5 Candidate Projection

## Implemented

The review-only D5 field projector reads persisted:

- EmploymentEvent
- EmploymentContract
- Employee
- PayrollD5ProfessionalEvidence
- Position

It verifies company and employment identity, source event
activity, non-reversal status, contract association,
profession evidence effective dates, position consistency,
and employee identity.

The projection uses values obtained from actual database
records rather than supplied stand-alone candidate facts.

## PostgreSQL acceptance

New isolated PostgreSQL tests exercise positive projection,
company isolation, professional-category disagreement,
date-effective evidence rejection, and reversal exclusion.

## Limits

The source SHA-256 in this component describes canonical
persisted ERP evidence only. It does not authenticate an
official normative source or classifier.

The component does not independently verify that a
professional evidence row was generated from a legally
approved classifier.

The two-row primary/secondary transition still requires
a separately persisted historical category change binding.

No official D5 rows are generated or filed.

OFFICIAL D5 GENERATION = BLOCKED.
PHASE 13 = OPEN.
