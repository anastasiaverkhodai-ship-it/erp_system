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

The ERP foundation and the first warehouse document posting engine are implemented.

### Core Platform

Implemented:

- FastAPI application
- PostgreSQL database
- Docker environment
- Async SQLAlchemy
- Alembic migrations
- Pydantic configuration
- Environment-based configuration

---

## Authentication

Implemented:

- User registration
- User login
- OAuth2 password login
- JWT access tokens
- JWT refresh token generation
- Argon2 password hashing
- Active user validation

Refresh token endpoint functionality is planned for a later stage.

---

## RBAC

Implemented:

- Users
- Roles
- Permissions
- Global user roles
- Role permissions
- Company membership
- Company-specific roles
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

Company-owned business data is isolated by `company_id`.

---

## Accounting Periods

Implemented:

- Company-specific accounting periods
- Year and month validation
- Unique periods per company
- Open/closed period handling
- Accounting period locking
- Posting-period validation

Document posting and reversal use accounting period validation.

---

## Chart of Accounts

Implemented:

- Company-specific Chart of Accounts
- Account creation
- Account reading
- Account updating
- Hierarchical accounts
- Parent account validation
- Company-specific account code uniqueness

Future accounting posting will build on this structure.

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

Products are deactivated instead of physically deleted so historical records remain valid.

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

Warehouses are deactivated instead of physically deleted so historical inventory records remain valid.

---

## Documents

Current warehouse document types:

```text
receipt
issue
adjustment
```

Current document statuses:

```text
draft
posted
reversed
cancelled
```

Implemented:

- Document creation
- Document lines
- Document listing
- Document details
- Draft editing
- Draft deletion
- Document posting
- Document reversal
- Company-specific document numbering

Current main lifecycle:

```text
DRAFT
  │
  ▼
POSTED
  │
  ▼
REVERSED
```

Posted documents cannot be edited or physically deleted.

---

## Inventory Posting Engine

The first transactional ERP posting contour is implemented.

Posting performs:

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
Stock balance locking
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

If any validation fails, the transaction is rolled back completely.

---

## Stock Ledger

Implemented:

```text
stock_ledger
```

Current movement types:

```text
receipt
issue
adjustment
reversal
```

`StockLedger` stores historical inventory movements created by posted documents.

Example:

```text
Receipt   +10
Issue      -7
--------------
Balance     3
```

---

## Stock Balance

Implemented:

```text
stock_balances
```

A current balance is maintained for each:

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

## Concurrency Protection

Inventory posting uses PostgreSQL row-level locking.

Relevant stock balance rows are locked with:

```text
SELECT ... FOR UPDATE
```

This prevents concurrent transactions from consuming the same inventory simultaneously.

Multi-stock operations acquire locks in deterministic order to reduce deadlock risk.

---

## Negative Stock Protection

Issue and negative adjustment operations are rejected if they would create negative inventory.

Multiple lines for the same product and warehouse are aggregated before stock validation.

This prevents multiple lines inside one document from independently consuming the same available balance.

---

## Document Reversal

Posted warehouse documents can be reversed.

Reversal:

- preserves original movements
- creates opposite `reversal` movements
- updates current stock balances
- validates the reversal accounting period
- validates resulting stock
- records `reversed_at`
- records `reversed_by`

Example:

```text
Original issue:  -6
Reversal:        +6
```

A document can only be reversed once.

Reversal is atomic and rolls back completely if validation fails.

---

## Audit

The project contains an `audit_logs` database foundation.

Full automatic audit-event integration is planned for later stages.

Important future audit events include:

- document posting
- document reversal
- accounting period changes
- accounting journal posting
- security-sensitive operations

---

## Current High-Level Architecture

```text
User
 │
 ▼
Authentication
 │
 ▼
RBAC
 │
 ▼
Company
 │
 ├── Accounting Periods
 ├── Chart of Accounts
 ├── Products
 ├── Warehouses
 │
 └── Documents
       │
       └── Document Lines
              │
              ▼
          Stock Ledger

Products + Warehouses
         │
         ▼
    Stock Balances
```

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

Detailed project documentation is available in:

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
development.md
```

---

## Planned Next Stage

The next major backend stage is the accounting posting engine.

Planned entities include:

```text
journal_entries
journal_entry_lines
```

Future integrated posting architecture:

```text
Business Document
       │
       ▼
Posting Engine
       │
       ├── Stock Ledger
       ├── Stock Balance
       │
       ├── Accounting Journal
       ├── General Ledger
       ├── VAT / Tax Registers
       └── Management Registers
```

Every accounting journal entry must satisfy double-entry accounting:

```text
SUM(Debit) = SUM(Credit)
```

Later stages will include:

- FIFO inventory costing
- Purchases
- Sales
- Warehouse transfers
- Inventory counts
- Cost of goods sold
- VAT accounting
- Ukrainian tax accounting
- Bank integration
- Financial statements
- Tax reports
- Management reports
- Full audit integration