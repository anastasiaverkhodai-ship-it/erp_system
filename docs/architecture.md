# ERP System Architecture

## 1. Overview

The ERP System is a multi-company enterprise resource planning platform designed primarily for Ukrainian businesses.

The system is intended to support three major accounting areas:

- Financial accounting
- Tax accounting
- Management accounting

The backend is implemented using FastAPI, PostgreSQL and asynchronous SQLAlchemy.

The system follows a modular architecture so that accounting, inventory, sales, purchasing, banking and reporting can evolve independently while sharing the same core infrastructure.

---

## 2. High-Level Architecture

```text
ERP Application
│
├── API Layer
│
├── Authentication & Authorization
│
├── Business Services
│
├── Accounting Core
│
├── Inventory Core
│
├── Database Layer
│
└── Background Processing
```

The API layer should remain relatively thin.

Complex business rules should be implemented inside service modules rather than directly inside API endpoints.

---

## 3. Backend Stack

The backend currently uses:

- Python 3.12
- FastAPI
- SQLAlchemy 2.0
- Async SQLAlchemy sessions
- PostgreSQL
- asyncpg
- Alembic
- Pydantic
- Uvicorn

Security components include:

- JWT access tokens
- JWT refresh tokens
- Argon2 password hashing
- OAuth2 Bearer authentication
- Role-Based Access Control

---

## 4. Application Layers

### API Layer

Location:

```text
app/api/
```

Responsibilities:

- HTTP endpoints
- Request validation
- Authentication dependencies
- Permission checks
- Calling business services
- Returning API responses

Business logic should not become concentrated inside this layer.

---

### Schema Layer

Location:

```text
app/schemas/
```

Pydantic schemas define:

- API request bodies
- API responses
- Validation rules
- Serialization rules

Database models and API schemas are kept separate.

---

### Model Layer

Location:

```text
app/models/
```

SQLAlchemy models represent persistent database entities.

Current major entities include:

```text
User
Company
Role
Permission
UserCompanyRole
AccountingPeriod
Account
Product
Warehouse
StockLedger
Document
AuditLog
```

---

### Service Layer

Location:

```text
app/services/
```

The service layer contains reusable business logic.

For example:

```text
accounting_period_service.py
```

contains logic for verifying whether an accounting period is open before an operation is allowed.

Future services will contain:

- document posting
- accounting entries
- inventory costing
- FIFO calculations
- VAT calculations
- bank reconciliation
- reporting logic

---

## 5. Multi-Company Architecture

The ERP supports multiple companies.

A user is not restricted to one company.

Example:

```text
User
│
├── Company A
│   └── Accountant
│
└── Company B
    └── Manager
```

Therefore authorization must consider both:

```text
user_id
+
company_id
+
role
+
permission
```

This allows the same user to have different access levels in different companies.

---

## 6. Authentication

Authentication uses JWT tokens.

The general flow is:

```text
Email + Password
       │
       ▼
Authentication
       │
       ▼
Access Token + Refresh Token
       │
       ▼
Bearer Token
       │
       ▼
Authenticated API Request
```

Passwords are hashed using Argon2 and are never stored in plain text.

---

## 7. Authorization and RBAC

The authorization architecture uses:

```text
User
  │
  ▼
Company
  │
  ▼
Role
  │
  ▼
Permission
```

Examples of roles:

```text
admin
director
accountant
manager
seller
```

Examples of permissions:

```text
users.read
users.create
users.update

companies.read
companies.update

accounting.periods.read
accounting.periods.manage

accounts.read
accounts.create
accounts.update
```

Permission checks for company resources are performed in the context of a specific `company_id`.

---

## 8. Accounting Architecture

The accounting subsystem follows this planned flow:

```text
Company
   │
   ▼
Accounting Period
   │
   ▼
Chart of Accounts
   │
   ▼
Journal Entry
   │
   ▼
Journal Entry Lines
   │
   ├── Debit
   └── Credit
   │
   ▼
General Ledger
   │
   ▼
Trial Balance
   │
   ▼
Financial Reports
```

Accounting periods and the Chart of Accounts are already part of the current foundation.

Journal entries and the posting engine are planned for the next accounting phase.

---

## 9. Accounting Periods

Each accounting period belongs to a company.

Example:

```text
Company 1
│
├── 2026-07
├── 2026-08
└── 2026-09
```

A period can be:

```text
open
closed
```

Closed periods are locked against normal accounting modifications.

Before a financial operation is posted, the system can call:

```text
ensure_period_open()
```

to verify that the operation date belongs to an open accounting period.

---

## 10. Chart of Accounts

Each company has its own Chart of Accounts.

Accounts are hierarchical.

Example:

```text
28  Товари
│
├── 281  Товари на складі
└── 282  Товари в торгівлі
```

Each account contains information such as:

```text
code
name
account_type
parent_id
is_active
```

The combination:

```text
company_id + code
```

must be unique.

This prevents duplicate account codes inside one company while allowing separate companies to maintain their own charts.

---

## 11. Inventory Architecture

Inventory is designed around movements rather than manually stored balances.

Instead of treating this as the accounting source:

```text
Product.quantity = 100
```

the system stores movements:

```text
+100 purchase receipt
-20 sale
-10 transfer
+5 adjustment
```

Stock balances can then be derived from the ledger.

The planned primary inventory costing method is FIFO.

---

## 12. Database Migrations

Database schema changes are managed exclusively through Alembic.

Typical workflow:

```text
Change SQLAlchemy model
        │
        ▼
alembic revision --autogenerate
        │
        ▼
Review migration
        │
        ▼
alembic upgrade head
```

Generated migrations should be reviewed before they are applied.

---

## 13. Auditability

ERP operations must be traceable.

Important actions should eventually record:

- user
- company
- operation
- timestamp
- affected entity
- previous state when required
- new state when required

Financial history must not be silently overwritten.

---

## 14. Planned Accounting Components

The next accounting components are:

```text
JournalEntry
JournalEntryLine
Posting Engine
General Ledger
Trial Balance
Financial Reports
```

An accounting transaction must always satisfy:

```text
Total Debit = Total Credit
```

Unbalanced journal entries must never be posted.

---

## 15. Planned ERP Modules

Future modules include:

```text
Accounting
Tax Accounting
VAT
Management Accounting
Purchases
Sales
Inventory
Customers
Suppliers
Bank
Cash
Financial Reporting
Tax Reporting
Background Jobs
Desktop UI
```

---

## 16. Architectural Principles

The project follows these principles:

1. Company data must remain isolated by company context.
2. Authorization must be enforced on the backend.
3. Financial operations must be auditable.
4. Posted accounting data should not be silently modified.
5. Closed periods must prevent normal posting or modification.
6. Accounting entries must balance.
7. Inventory balances should originate from movements.
8. Business logic belongs primarily in services.
9. Database schema changes must use Alembic.
10. Security-sensitive information must never be stored in plain text.