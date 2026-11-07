# ERP Phase 13.14 — Combined 4DF Source Binding

## Delivery

This service integrates the existing 4DF dated-slice
binding validator with the combined D1/4DF/D6 review.

For supplied 4DF groups, it validates:

- Company and payroll-period scope
- Current payroll calculation coverage
- Payroll employee identity
- Complete, unique dated slices
- Payment dates and evidence references
- Financial manifest agreement
- Structural source-binding checksum

When groups are not supplied, the result remains
NOT_VERIFIED.

## Explicit limitations

The source_sha256 carried by a dated slice is not
independently authenticated against its original
persisted source by this integration.

Therefore SLICE_TOPOLOGY_VERIFIED is not equivalent
to source authenticity or statutory readiness.

NET payments are never interpreted as gross paid
income for official 4DF fields.

No regulatory classification, correction XML,
filing approval or statutory row generation occurs.

All official form generation remains BLOCKED.

Phase 13 is OPEN.
