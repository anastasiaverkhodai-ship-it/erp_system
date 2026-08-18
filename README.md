# ERP System

ERP System is a backend platform for a full-scale enterprise resource planning and accounting system.

The project is designed primarily for Ukrainian businesses and is intended to support:

- Financial accounting
- Tax accounting
- Management accounting
- Inventory and warehouse management
- Sales
- Purchases
- Banking
- Cash operations
- Financial reporting
- Tax reporting
- Role-Based Access Control
- Multi-company operation

The architecture is inspired by enterprise accounting systems such as 1C UTP while being implemented as an independent modern ERP platform.

---

## Technology Stack

### Backend

- Python 3.12
- FastAPI
- SQLAlchemy 2.0 Async
- Pydantic v2
- Alembic
- Uvicorn

### Database

- PostgreSQL 16
- asyncpg

### Infrastructure

- Docker
- Docker Compose
- Redis

### Security

- JWT authentication
- Access tokens
- Refresh tokens
- Argon2 password hashing
- Role-Based Access Control
- Global permissions
- Company-scoped permissions

---

## Current Project Status

Version: `0.1`

Implemented foundations include:

```text
Authentication
RBAC
Companies
Accounting Periods
Chart of Accounts

Products
Warehouses
Documents
Document Lines

Stock Ledger
Stock Balances
Warehouse Posting
Warehouse Reversal

Journal Entries
Journal Entry Lines
Accounting Posting
Accounting Reversal
```

---

## Authentication

Implemented:

- User registration
- User login
- OAuth2 password authentication
- JWT access tokens
- JWT refresh token generation
- Argon2 password hashing
- Active user validation

A dedicated refresh endpoint is planned for a later stage.

---

## RBAC

Implemented:

- Users
- Roles
- Permissions
- Global roles
- Company-specific roles
- Role permissions
- Company-specific permission enforcement
- Active/inactive company access

Current standard roles:

```text
admin
director
accountant
manager
seller
```

---

## Companies

Implemented:

- Company creation
- Company reading
- Company updating
- Company user management
- Company role assignment
- Company access deactivation

Business data is isolated by `company_id`.

---

## Accounting Periods

Implemented:

- Company-specific accounting periods
- Unique year/month periods
- Open/closed period handling
- Period locking
- Posting-period validation

Both warehouse posting and accounting posting use accounting period validation.

Reversal operations also validate their reversal date against the accounting period.

---

## Chart of Accounts

Implemented:

- Company-specific accounts
- Account creation
- Account reading
- Account updating
- Hierarchical accounts
- Parent account validation
- Company-specific account code uniqueness
- Account activation/deactivation

---

## Products

Implemented:

- Company-specific products
- Product creation
- Product listing
- Product details
- Product updating
- Product activation/deactivation
- Company-specific SKU uniqueness

Products are normally deactivated instead of physically deleted.

---

## Warehouses

Implemented:

- Company-specific warehouses
- Warehouse creation
- Warehouse listing
- Warehouse details
- Warehouse updating
- Warehouse activation/deactivation
- Company-specific warehouse name uniqueness

Warehouses are normally deactivated instead of physically deleted.

---

## Warehouse Documents

Current warehouse document types:

```text
receipt
issue
adjustment
```

Current statuses:

```text
draft
posted
reversed
cancelled
```

Implemented:

- Draft creation
- Draft editing
- Draft deletion
- Document listing
- Document details
- Posting
- Reversal
- Company-specific document numbering

Main lifecycle:

```text
DRAFT
  │
  ▼
POSTED
  │
  ▼
REVERSED
```

Posted warehouse documents cannot be destructively edited or deleted.

---

## Inventory Posting Engine

Warehouse posting performs:

```text
Document validation
        │
        ▼
Accounting period validation
        │
        ▼
Product / warehouse validation
        │
        ▼
Stock delta calculation
        │
        ▼
StockBalance locking
        │
        ▼
Negative stock validation
        │
        ▼
StockBalance update
        │
        ▼
StockLedger movements
        │
        ▼
Document POSTED
```

Posting is atomic.

Failed posting operations are rolled back completely.

---

## Stock Ledger

`stock_ledger` stores historical inventory movements.

Current movement types:

```text
receipt
issue
adjustment
reversal
```

Example:

```text
Receipt   +10
Issue      -7
--------------
Balance     3
```

---

## Stock Balance

`stock_balances` stores the current operational inventory quantity for:

```text
company
+
product
+
warehouse
```

The expected invariant is:

```text
StockBalance.quantity
=
SUM(StockLedger.quantity)
```

for the same company, product and warehouse.

---

## Inventory Concurrency Protection

Warehouse posting uses PostgreSQL row-level locking:

```text
SELECT ... FOR UPDATE
```

This prevents concurrent operations from consuming the same available stock.

Multi-balance operations acquire locks in deterministic order to reduce deadlock risk.

---

## Negative Stock Protection

Issues and negative adjustments are rejected if they would produce negative stock.

Multiple document lines affecting the same product and warehouse are aggregated before stock validation.

---

## Warehouse Reversal

Posted warehouse documents can be reversed.

Reversal:

- preserves original ledger movements
- creates opposite `reversal` movements
- updates StockBalance
- validates the reversal accounting period
- prevents resulting negative stock
- records `reversed_at`
- records `reversed_by`

A warehouse document can only be reversed once.

---

# Accounting Posting v1

The ERP now contains the first double-entry accounting posting engine.

Implemented tables:

```text
journal_entries
journal_entry_lines
```

---

## Journal Entries

Current journal statuses:

```text
draft
posted
reversed
```

Implemented operations:

```text
GET     journal entry list
GET     journal entry
POST    create draft
PATCH   update draft
DELETE  delete draft
POST    post journal entry
POST    reverse journal entry
```

Main lifecycle:

```text
DRAFT
  │
  ▼
POSTED
  │
  ▼
REVERSED
```

---

## Journal Entry Lines

Each accounting line contains:

```text
account_id
debit
credit
description
```

Exactly one side of a line must be positive:

```text
Debit > 0, Credit = 0
```

or:

```text
Credit > 0, Debit = 0
```

A line cannot contain values on both sides.

A line also cannot contain zero on both sides.

---

## Double-Entry Validation

A draft may temporarily be unbalanced.

For example:

```text
Debit  = 1000
Credit = 900
```

may exist as a draft.

However, posting requires:

```text
SUM(Debit) = SUM(Credit)
```

and:

```text
SUM(Debit) > 0
```

Example of a valid posted journal entry:

```text
Dr 281   1000.00
Cr 631   1000.00
```

---

## Accounting Account Validation

New accounting posting validates that every account:

- exists
- belongs to the same company as the journal entry
- is active

A Company 1 journal entry cannot post against a Company 2 account.

---

## Accounting Period Validation

A journal entry can only be posted when its `entry_date` belongs to an open accounting period.

Accounting reversal separately validates its `reversal_date`.

---

## Accounting Posting Concurrency

The JournalEntry row is locked using PostgreSQL row-level locking during posting.

This protects lifecycle transitions such as:

```text
DRAFT → POSTED
```

from conflicting operations.

---

## Accounting Reversal

Posted journal entries are corrected through reversal rather than destructive modification.

Example:

```text
Original:

Dr 281   1000
Cr 631   1000
```

Reversal:

```text
Dr 631   1000
Cr 281   1000
```

The reversal is stored as a new posted JournalEntry.

It references the original through:

```text
reversal_of_id
```

The original becomes:

```text
status = reversed
reversed_at = timestamp
reversed_by = user_id
```

A unique constraint prevents multiple reversal entries for the same original journal entry.

A reversal journal entry itself cannot be reversed through the current reversal service.

---

## Historical Account Reversal

Accounting reversal does not require the historical accounts to remain active.

This allows an old valid accounting posting to be reversed even if one of its accounts was later deactivated.

The accounts must still belong to the correct company.

---

## Current Accounting API

```text
GET
/api/v1/companies/{company_id}/journal-entries

POST
/api/v1/companies/{company_id}/journal-entries

GET
/api/v1/companies/{company_id}/journal-entries/{journal_entry_id}

PATCH
/api/v1/companies/{company_id}/journal-entries/{journal_entry_id}

DELETE
/api/v1/companies/{company_id}/journal-entries/{journal_entry_id}

POST
/api/v1/companies/{company_id}/journal-entries/{journal_entry_id}/post

POST
/api/v1/companies/{company_id}/journal-entries/{journal_entry_id}/reverse
```

---

## Current Architecture

```text
Company
│
├── Accounting Periods
├── Chart of Accounts
│
├── Products
├── Warehouses
│
├── Documents
│     │
│     └── Document Lines
│            │
│            └── Stock Ledger
│
├── Stock Balances
│
└── Journal Entries
       │
       └── Journal Entry Lines
              │
              └── Accounts
```

Warehouse posting and accounting posting currently exist as separate operational contours.

Automatic generation of JournalEntry records from warehouse documents is not yet implemented.

This is intentional because a generic receipt, issue, or adjustment does not by itself contain enough business information to determine every accounting correspondence correctly.

---

## Audit

The project contains an `audit_logs` foundation.

Full automatic audit-event integration remains planned.

Important future audit events include:

- warehouse document posting
- warehouse document reversal
- journal entry posting
- journal entry reversal
- accounting period changes
- security-sensitive operations

---

## Project Structure

```text
erp_system/
│
├── app/
│   ├── api/
│   │   └── v1/
│   ├── core/
│   ├── models/
│   ├── schemas/
│   ├── services/
│   └── main.py
│
├── alembic/
├── docs/
├── scripts/
├── alembic.ini
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Documentation

Detailed documentation is available in:

```text
docs/
```

Important documents include:

```text
architecture.md
authentication.md
database.md
rbac.md
accounting_periods.md
chart_of_accounts.md
documents.md
accounting.md
development.md
```

---

## Planned Next Stages

Major future stages include:

```text
Document → Accounting posting rules
Purchase documents
Sales documents
Business partners
Contracts
FIFO inventory costing
Cost of goods sold
Warehouse transfers
Inventory counts
VAT accounting
Ukrainian tax accounting
Bank integration
Bank reconciliation
Financial statements
Tax reporting
Management reporting
Full audit integration
```

Future business documents will eventually be capable of creating consistent movements across:

```text
Stock Ledger
Stock Balance
Accounting Journal
General Ledger
Tax / VAT Registers
Management Registers
```