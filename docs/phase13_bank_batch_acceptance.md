# Phase 13 — persistent bank batch acceptance, 2026-11-16

Baseline: ad0f0adb523432961ac43b760f05283fd2a4d901.

Implemented persistent bank batches and immutable allocations for one outgoing UAH
statement line and two or more fully allocated posted payouts for one employee.
The existing reconciliation ledger records one batch event and one active line claim.
Company locking serializes batch and individual payroll reconciliation operations.
Posting and reversal use savepoints; the caller owns the outer commit.

Exact replay (including reordered members) returns the existing batch. Changed
request payloads fail. Replay after reversal returns REVERSED and never reposts;
a new request key permits a new match after the original has been reversed.
Reversal appends history and releases the whole batch. It does not reverse wages,
create a transfer, or generate additional accounting journals. Reversing a payout's
journal while it is matched in an active batch is rejected.

PostgreSQL protects batch/allocation history, seals allocations once posted, and
checks reconciliation totals and reversal provenance. Migration downgrade refuses
to discard batch history. This is a service-level feature; no new UI/API endpoint
or production migration is included.

## Final gate counters

- 61 tests passed; 0 failed; 0 skipped.
- 17 new real financial batch PostgreSQL scenarios plus 1 migration round trip.
- 23 PostgreSQL-backed checks in total; 38 focused regression checks.
- Verified duplicate, amount mismatch, invalid source, idempotency conflict,
  member reuse, cross-company/employee, concurrent replay and competing requests.
- Injected final-write failure: posting and reversal both roll back atomically.
- Positive fixture: 1 bank line, 2 payouts, 2 allocations, 1 active reconciliation.
- Additional payroll journals from matching/unmatching: 0.
- Copy upgrade, downgrade/upgrade round trip and Alembic schema check: PASS.
- Disposable database removed: PASS.
- Original unrelated files: 119; changed: 0 (SHA-256 comparison).
- Production full logical dump before/after: identical, SHA-256
  3a37fbbac88d48308d0a9ea8944db632424bac483e9ec4f3faf8b5c2c9de429c.
- Production Alembic revision unchanged: p13d6grant261105.
- Official statutory generation permissions: BLOCKED; no expansion.

Evidence for this run: `/var/folders/yf/wk8rhg8j5h7f_9d7ds2mwz680000gp/T/erp_phase13_bank_gate_gf3b4gx3` (tests.log,
tests.xml, protected.json and summary.json; no production dump saved).

Re-run the complete gate with:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m scripts.run_phase13_bank_batch_gate
```

## Phase 13 progress

Bank Batch Completion is accepted within the scope above. Of the three remaining
large stages in the previous handoff, one is now complete and two remain:
statutory reports / remaining confirmed gaps, and full Phase 13 final acceptance.
Phase 13 remains OPEN. The former 85% estimate was not a measured acceptance
percentage; this gate does not establish a new exact percentage or approve
statutory generation. Commit/push status is recorded by Git after gate success.
