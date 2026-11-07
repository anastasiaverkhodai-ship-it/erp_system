# Phase 13.14 — Encrypted Payroll Tax Document Custody

## Scope

AES-256-GCM authenticated encryption stores document
ciphertext in PostgreSQL. The encryption key is not
persisted with the document and must be supplied from
an external deployment secret.

Authenticated additional data binds the ciphertext to
company, evidence identity and document SHA-256.

The service checks:
- active company-scoped `employees.manage` permission;
- active, posted payroll payment and current correction;
- existing SHA-256 integrity record;
- hash and document length on retrieval;
- reviewer permission `journal_entries.approve`;
- independent reviewer identity.

PostgreSQL validates company-scoped permissions for
custody INSERT and review INSERT. The custody record
is append-only.

## Deployment constraints

Environment variables:
- ERP_PAYROLL_CUSTODY_KEY_B64: base64-encoded 32-byte key
- ERP_PAYROLL_CUSTODY_KEY_ID: external key identifier

Production key provisioning, rotation, backup,
disaster recovery and retention policy still require
operational acceptance.

The application service does not expose a public API
endpoint in this delivery.

The PostgreSQL runtime role must not own protected
tables or have permission to disable triggers.

## Explicit non-claims

The source document and issuer are not externally
authenticated. Internal review is not regulatory approval.

Factually withheld PIT and military levy remain
unverified at payment level.

Official 4DF and D6 generation stays BLOCKED.
Phase 13 stays OPEN.
