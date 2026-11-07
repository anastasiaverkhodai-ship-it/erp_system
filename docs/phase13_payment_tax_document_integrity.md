# Phase 13.14 — Payment Tax Document Integrity

## Implemented

Two append-only PostgreSQL tables record:

1. SHA-256 document integrity checks.
2. Internal independent-review events.

The integrity service computes SHA-256 from actual
document bytes, rather than accepting a supplied hash
as proof.

The computed digest must match the existing evidence
registry. Documents must contain between 1 byte and
5 MiB of data.

The service rechecks the payroll calculation, active
payment, posted journal and current correction version.

The independent-review service rejects a reviewer
who created the candidate, submitted the evidence
or performed the integrity check.

A PostgreSQL trigger independently rejects
the same self-review conditions.

Both tables reject UPDATE and DELETE operations.

## Explicit limitations

Document content is not durably stored in this delivery.

A matching digest establishes integrity relative to
the previously registered digest. It does not by itself
establish authenticity or issuer authority.

An internal review does not prove that PIT or military
levy was actually withheld during a payroll payment.

The positive authorized-review PostgreSQL scenario
and durable document custody remain outstanding.

The PostgreSQL runtime role must also be separated
from the owner of protected database tables.

## Regulatory status

Independent issuer verification: NOT VERIFIED.
Payment-specific withholding: NOT VERIFIED.
4DF gross paid: NOT APPROVED.
Official statutory generation: BLOCKED.
Phase 13: OPEN.
