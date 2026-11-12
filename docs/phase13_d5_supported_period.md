# Supported D5 profession verification period

User decision: 2026-11-11.

Profession verification supports event/report dates on or after 2026-01-01.
Earlier event dates are explicitly unsupported. The verified import boundary
must not be presented as the legal introduction date of an existing profession.
Older source documents remain usable if their provisions are still applicable.
This decision does not approve any previously unverified classifier dataset.

Acceptance requires a verified classifier state on 2026-01-01 and all applicable
subsequent changes, including dates and corrections. A complete history back to
2010 is no longer an acceptance requirement. Earlier documents may still be used
to establish the opening state; missing amendment 1 is not by itself a blocker
if a later authoritative consolidated state can be fully reconciled.

Implemented boundary: verify_d5_classifier and payroll_d5_source_bound_readiness.
Tests: 49 classifier/import/source-bound tests passed, including rejection of a
2025 event even with matching reviewed evidence and acceptance of the 2026 boundary
with an older effective source. This is not normative dataset approval.

2026-11-12 update: the complete civil content reconciliation and pinned primary
sources are in `normative_evidence/2026_11_12/REVIEW.md`. The full D5 test selection
passes (475 tests, including 27 PostgreSQL cases on an isolated cluster).
Normative dated approval remains explicitly separate and is not yet granted.
