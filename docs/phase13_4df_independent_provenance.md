# ERP 13.14 — Independent 4DF Financial Provenance

## Scope

This module independently reloads current PostgreSQL
payroll calculations and frozen statutory components.

It reproduces the SOURCE_EVIDENCE_V1 fingerprint used
by the dated 4DF allocation writer.

Every supplied dated financial evidence slice must
contain the independently recomputed source hash.

The internal financial-group fingerprint is also
recomputed from the existing group schema.

Coverage, employee identity and previous combined
D1/4DF/D6 reconciliation must remain valid.

## Posted payment provenance

When reviewed allocations are provided, the existing
PostgreSQL-backed posted payment verifier is invoked.

Unprovided posted-payment evidence is explicitly
NOT VERIFIED. Source fingerprint verification alone
is not evidence of an actual payment.

Previous PostgreSQL real-payment tests are included
in the acceptance regression.

## Limitations

4DF paid_amount is NET disbursement evidence.

Gross paid income is not inferred from NET payment,
PIT remittance or military levy remittance.

Statutory income categories and official fields are
not approved. No official rows are generated.

A SHA-256 checksum is an integrity comparison, not
an independently signed or immutable legal record.

OFFICIAL STATUTORY GENERATION = BLOCKED.
PHASE 13 = OPEN.
