# Database Architecture

## 1. Overview

The ERP System uses PostgreSQL as its primary relational database.

Database access is implemented with SQLAlchemy 2.0 using asynchronous sessions and the `asyncpg` PostgreSQL driver.

Database schema changes are managed through Alembic migrations.

---

## 2. Current Database Tables

The current database contains the following major tables:

```text
users
companies

roles
permissions
user_roles
role_permissions

user_companies
user_company_roles

accounting_periods
accounts

products
warehouses
stock_ledger

documents
audit_logs
```

---

## 3. High-Level Relationships

```text
                        ┌─────────────┐
                        │    users    │
                        └──────┬──────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
             user_companies      user_company_roles
                    │                     │
                    ▼                     ▼
              ┌───────────┐          ┌─────────┐
              │ companies │          │  roles  │
              └─────┬─────┘          └────┬────┘
                    │                     │
              ┌─────┴───────┐             ▼
              │             │      role_permissions
              ▼             ▼             │
     accounting_periods   accounts        ▼
                                      permissions
```

The company is one of the main boundaries of ERP data.

Business and accounting records should normally belong to a specific company.

---

## 4. Users

Table:

```text
users
```

Represents ERP users.

Important responsibilities:

- authentication identity
- user profile
- account activation
- participation in companies
- role assignment

Passwords are not stored directly.

Only password hashes are stored.

---

## 5. Companies

Table:

```text
companies
```

Represents legal entities or businesses managed inside the ERP.

A company acts as a data boundary for:

- users
- roles
- accounting periods
- accounting accounts
- documents
- warehouses
- future journals and ledgers

---

## 6. User and Company Access

### user_companies

Table:

```text
user_companies
```

Represents basic access between users and companies.

Conceptually:

```text
User ↔ Company
```

A user may have access to multiple companies.

---

### user_company_roles

Table:

```text
user_company_roles
```

Assigns roles to users inside specific companies.

Main key structure:

```text
user_id
company_id
role_id
```

The table also contains:

```text
is_active
assigned_at
```

This allows the same user to have different roles in different companies.

Example:

```text
User 15
│
├── Company 1 → accountant
└── Company 2 → manager
```

Deactivating a company role does not require deleting historical user information.

---

## 7. Roles

Table:

```text
roles
```

Current roles include:

```text
admin
director
accountant
manager
seller
```

Roles group permissions together.

---

## 8. Permissions

Table:

```text
permissions
```

Permissions represent individual capabilities.

Examples:

```text
users.read
users.create
users.update

companies.create
companies.read
companies.update
products.read
products.create
products.update

warehouse.read
warehouse.create

documents.read
documents.create
documents.approve

reports.read

accounting.periods.read
accounting.periods.manage

accounts.read
accounts.create
accounts.update
```

---

## 9. Role Permissions

Table:

```text
role_permissions
```

Many-to-many relationship:

```text
Role ↔ Permission
```

Example:

```text
accountant
│
├── accounting.periods.read
├── accounting.periods.manage
├── accounts.read
├── accounts.create
└── accounts.update
```

Permissions are checked by the backend rather than trusted from the client application.

---

## 10. Global User Roles

Table:

```text
user_roles
```

Represents the earlier/global user-to-role relationship.

The ERP also uses `user_company_roles` for company-specific authorization.

As the architecture evolves, company-scoped authorization should be preferred for company-owned business resources.

---

## 11. Accounting Periods

Table:

```text
accounting_periods
```

Each period belongs to one company.

Important fields:

```text
id
company_id
year
month
start_date
end_date
status
is_locked
created_at
closed_at
```

Relationship:

```text
companies
    │
    └── accounting_periods
```

The combination:

```text
company_id + year + month
```

is unique.

The database also enforces:

```text
1 <= month <= 12
```

A period can be locked to prevent normal accounting modifications.

---

## 12. Chart of Accounts

Table:

```text
accounts
```

Represents accounting accounts belonging to a company.

Important fields:

```text
id
company_id
code
name
account_type
parent_id
is_active
created_at
```

Relationship:

```text
companies
    │
    └── accounts
```

Account codes are unique inside each company:

```text
UNIQUE(company_id, code)
```

---

## 13. Account Hierarchy

`accounts.parent_id` references:

```text
accounts.id
```

This creates a self-referencing hierarchy.

Example:

```text
28  Товари
│
├── 281 Товари на складі
└── 282 Товари в торгівлі
```

Conceptually:

```text
accounts
   ▲
   │ parent_id
   │
accounts
```

A parent account must belong to the same company. This rule is currently enforced by application logic.

---

## 14. Products

Table:

```text
products
```

Represents products or inventory items.

Products will later participate in:

- purchases
- sales
- warehouse movements
- FIFO costing
- inventory valuation
- accounting posting

---

## 15. Warehouses

Table:

```text
warehouses
```

Represents physical or logical storage locations.

Future warehouse operations may include:

```text
receipts
shipments
transfers
adjustments
inventory counts
```

---

## 16. Stock Ledger

Table:

```text
stock_ledger
```

The stock ledger stores inventory movements.

The intended architecture is movement-based:

```text
Receipt       +100
Sale           -20
Transfer       -10
Adjustment      +5
```

Current inventory should ultimately be derived from ledger movements rather than manually maintained balances.

---

## 17. Documents

Table:

```text
documents
```

Represents the foundation for ERP business documents.

Future document types may include:

```text
purchase invoice
sales invoice
goods receipt
goods issue
warehouse transfer
bank transaction
cash transaction
inventory adjustment
```

Documents will eventually integrate with the posting engine.

---

## 18. Audit Logs

Table:

```text
audit_logs
```

Used as the foundation for system auditability.

Important ERP actions should eventually be traceable by:

```text
user
company
timestamp
operation
entity
changes
```

Audit history is especially important for financial and accounting operations.

---

## 19. Planned Accounting Tables

The accounting database will later be extended with entities such as:

```text
journal_entries
journal_entry_lines
```

Expected relationship:

```text
Company
   │
   ▼
Journal Entry
   │
   ├─────────────┐
   ▼             ▼
Debit Line    Credit Line
   │             │
   └──────┬──────┘
          ▼
       Account
```

Each posted journal entry must satisfy:

```text
SUM(debit) = SUM(credit)
```

---

## 20. Planned Data Flow

A future business document may follow this flow:

```text
Business Document
       │
       ▼
Validation
       │
       ▼
Accounting Period Check
       │
       ▼
Posting Engine
       │
       ├── Accounting Ledger
       │
       ├── Stock Ledger
       │
       └── Tax Registers
       │
       ▼
Reports
```

This allows one business operation to create consistent movements across different ERP registers.

---

## 21. Database Design Principles

The database follows these principles:

1. Company-owned records must be isolated by company.
2. Financial history should remain auditable.
3. Referential integrity should be enforced with foreign keys where appropriate.
4. Important uniqueness rules should also exist at database level.
5. Accounting periods control whether dated financial operations may be modified.
6. Account codes are unique within a company.
7. Inventory uses movement-based accounting.
8. Schema changes are performed through Alembic migrations.
9. Application validation does not replace important database constraints.
10. Future accounting entries must preserve double-entry accounting rules.