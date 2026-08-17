# Database Architecture

## 1. Overview

The ERP System uses PostgreSQL as its primary relational database.

Database access is implemented with SQLAlchemy 2.0 using asynchronous sessions and the `asyncpg` PostgreSQL driver.

Database schema changes are managed through Alembic migrations.

The database is designed around the following principles:

- multi-company isolation
- transactional document posting
- auditable operational history
- explicit current stock balances
- company-scoped RBAC
- accounting period control
- reversal instead of destructive changes to posted documents

---

## 2. Current Database Tables

The current database contains:

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

documents
document_lines

stock_ledger
stock_balances

audit_logs
```

---

## 3. High-Level Architecture

```text
Company
│
├── Users / Roles
│
├── Accounting Periods
│
├── Chart of Accounts
│
├── Products
│
├── Warehouses
│
└── Documents
      │
      └── Document Lines
             │
             ├── Product
             ├── Warehouse
             │
             └── Stock Ledger

Products + Warehouses
          │
          ▼
     Stock Balances
```

A company is one of the primary data boundaries of the ERP.

Operational and accounting data belonging to one company must not be mixed with data belonging to another company.

---

## 4. Users

Table:

```text
users
```

Represents ERP users.

Important responsibilities:

- authentication identity
- profile information
- account activation
- company access
- role assignment

Passwords are never stored directly.

Only password hashes are stored.

---

## 5. Companies

Table:

```text
companies
```

Represents legal entities or businesses managed by the ERP.

Company-scoped entities currently include:

```text
accounting_periods
accounts
products
warehouses
documents
stock_ledger
stock_balances
```

Future accounting journals and tax registers will also be company-scoped.

---

## 6. User Company Access

### user_companies

Represents access between users and companies.

```text
User ↔ Company
```

A user may have access to multiple companies.

### user_company_roles

Assigns roles inside a specific company.

Important fields:

```text
user_id
company_id
role_id
is_active
assigned_at
```

Example:

```text
User 15
│
├── Company 1 → accountant
└── Company 2 → manager
```

A user's role may therefore differ between companies.

---

## 7. Roles and Permissions

Tables:

```text
roles
permissions
role_permissions
user_roles
user_company_roles
```

Current standard roles:

```text
admin
director
accountant
manager
seller
```

Roles group permissions together.

The current role set is an initial ERP configuration. Additional roles may be added later.

---

## 8. Accounting Periods

Table:

```text
accounting_periods
```

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

Each period belongs to one company.

The following combination is unique:

```text
company_id + year + month
```

The database also enforces:

```text
1 <= month <= 12
```

Document posting and reversal validate that the relevant operation date belongs to an open accounting period.

---

## 9. Chart of Accounts

Table:

```text
accounts
```

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

Account codes are unique inside one company:

```text
UNIQUE(company_id, code)
```

Accounts support hierarchical structure through:

```text
parent_id → accounts.id
```

Example:

```text
28  Товари
│
├── 281 Товари на складі
└── 282 Товари в торгівлі
```

The application validates that a parent account belongs to the same company.

Deep circular hierarchy prevention is planned for a later stage.

---

## 10. Products

Table:

```text
products
```

Important fields:

```text
id
company_id
name
sku
is_active
```

Each product belongs to one company.

SKU is unique inside a company:

```text
UNIQUE(company_id, sku)
```

Therefore this is valid:

```text
Company 1 → ABC-001
Company 2 → ABC-001
```

but this is not:

```text
Company 1 → ABC-001
Company 1 → ABC-001
```

Products are normally deactivated instead of physically deleted because historical documents and stock movements may reference them.

---

## 11. Warehouses

Table:

```text
warehouses
```

Important fields:

```text
id
company_id
name
is_active
```

Each warehouse belongs to one company.

Warehouse names are unique inside one company:

```text
UNIQUE(company_id, name)
```

Warehouses are normally deactivated instead of physically deleted.

---

## 12. Documents

Table:

```text
documents
```

Important fields:

```text
id
company_id
number
document_type
document_date
status
created_by
created_at
posted_at
reversed_at
reversed_by
```

Document numbers are unique inside a company:

```text
UNIQUE(company_id, number)
```

Current document types:

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

`cancelled` is reserved for future lifecycle functionality.

The current main lifecycle is:

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

## 13. Document Lines

Table:

```text
document_lines
```

Important fields:

```text
id
document_id
product_id
warehouse_id
quantity
price
```

Quantities and prices use:

```text
NUMERIC(18,4)
```

instead of floating-point values.

Relationship:

```text
Document
   │
   └── DocumentLine
          ├── Product
          ├── Warehouse
          ├── Quantity
          └── Price
```

Deleting a draft document removes its lines using:

```text
ON DELETE CASCADE
```

Products and warehouses use restrictive foreign keys so historical references cannot be silently destroyed.

---

## 14. Stock Ledger

Table:

```text
stock_ledger
```

The stock ledger stores the historical inventory movements created by posted documents.

Important fields:

```text
id
company_id
document_id
document_line_id
product_id
warehouse_id
quantity
movement_type
movement_date
created_at
```

Quantity uses:

```text
NUMERIC(18,4)
```

Current movement types:

```text
receipt
issue
adjustment
reversal
```

Movement sign convention:

```text
receipt      positive
issue        negative
adjustment   positive or negative
reversal     opposite sign of original movement
```

Example:

```text
Receipt   +10
Issue      -7
--------------
Balance     3
```

Normal operational inventory movements originate from posted documents.

Historical ledger movements should not be manually rewritten to alter inventory balances.

---

## 15. Stock Balances

Table:

```text
stock_balances
```

Stores the current operational stock quantity.

Important fields:

```text
id
company_id
product_id
warehouse_id
quantity
updated_at
```

Exactly one balance row exists for each combination of:

```text
company_id
product_id
warehouse_id
```

enforced by:

```text
UNIQUE(company_id, product_id, warehouse_id)
```

The expected invariant is:

```text
StockBalance.quantity
=
SUM(StockLedger.quantity)
```

for the same company, product and warehouse.

`StockLedger` is the movement history.

`StockBalance` is the current operational state and is also used for concurrency locking.

---

## 16. Document Posting

Posting converts a draft document into operational stock movements.

Current flow:

```text
POST /documents/{id}/post
          │
          ▼
   Lock Document
          │
          ▼
 status == DRAFT
          │
          ▼
Accounting Period Check
          │
          ▼
 Validate Products
          │
          ▼
Validate Warehouses
          │
          ▼
Validate Quantities
          │
          ▼
Calculate Stock Deltas
          │
          ▼
Lock StockBalance
          │
          ▼
Validate Resulting Stock
          │
          ▼
Update StockBalance
          │
          ▼
Create StockLedger Movements
          │
          ▼
Document → POSTED
          │
          ▼
        COMMIT
```

If any validation fails:

```text
ROLLBACK
```

No partial posting is allowed.

---

## 17. PostgreSQL Concurrency Control

Posting uses PostgreSQL row-level locking.

The relevant balance row is selected using:

```text
SELECT ... FOR UPDATE
```

Example:

```text
Current stock = 8

Transaction A → ISSUE 6
Transaction B → ISSUE 6
```

Correct behavior:

```text
Transaction A
│
├── locks StockBalance
├── 8 - 6 = 2
└── COMMIT

Transaction B
│
├── waits for the lock
├── reads new quantity = 2
├── 2 - 6 < 0
└── ROLLBACK
```

Final result:

```text
StockBalance = 2
StockLedger SUM = 2
```

When multiple balance rows are involved, locks are acquired in deterministic order to reduce deadlock risk.

---

## 18. Document Reversal

Posted documents are not physically deleted.

They are reversed.

Current flow:

```text
POST /documents/{id}/reverse
          │
          ▼
   Lock Document
          │
          ▼
 status == POSTED
          │
          ▼
Check Reversal Period
          │
          ▼
Load Original Movements
          │
          ▼
Calculate Opposite Movements
          │
          ▼
Lock StockBalance
          │
          ▼
Validate Resulting Stock
          │
          ▼
Update StockBalance
          │
          ▼
Create REVERSAL Movements
          │
          ▼
Document → REVERSED
          │
          ├── reversed_at
          └── reversed_by
          │
          ▼
        COMMIT
```

Example:

```text
Original ISSUE
-6

Reversal
+6
```

The original movement remains in the ledger.

The reversal is stored as a separate new movement.

---

## 19. Reversal Stock Protection

A reversal is rejected if it would create negative inventory.

Example:

```text
Receipt +10
Issue    -7

Current stock = 3
```

Reversing the original receipt would produce:

```text
3 - 10 = -7
```

Therefore the operation is rejected and rolled back.

The following remain unchanged:

```text
Document
StockLedger
StockBalance
```

A reversed document cannot be reversed again.

---

## 20. Document Integrity Rules

### DRAFT

```text
GET      allowed
PATCH    allowed
DELETE   allowed
POST     allowed
```

### POSTED

```text
GET      allowed
PATCH    rejected
DELETE   rejected
POST     again rejected
REVERSE  allowed with permission
```

### REVERSED

```text
GET             allowed
PATCH           rejected
DELETE          rejected
second REVERSE  rejected
```

---

## 21. Audit Logs

Table:

```text
audit_logs
```

Provides a foundation for system auditability.

Full automatic audit integration is still planned.

Important ERP operations should eventually record:

```text
user
company
timestamp
operation
entity
changes
```

Especially important operations include:

```text
document posting
document reversal
accounting period changes
accounting journal posting
```

---

## 22. Planned Accounting Tables

Future accounting tables include:

```text
journal_entries
journal_entry_lines
```

Expected structure:

```text
Document
   │
   ▼
JournalEntry
   │
   ├── Debit
   └── Credit
```

Every posted journal entry must satisfy:

```text
SUM(debit) = SUM(credit)
```

---

## 23. Planned Integrated Posting

The warehouse posting engine is the first operational posting contour.

Future integrated posting will extend the same concept to:

```text
Business Document
       │
       ▼
Validation
       │
       ▼
Accounting Period
       │
       ▼
Posting Engine
       │
       ├── Stock Ledger
       ├── Stock Balance
       │
       ├── Accounting Journal
       ├── General Ledger
       │
       ├── Tax / VAT Registers
       └── Management Registers
```

---

## 24. Planned Inventory Extensions

Future inventory development may include:

```text
warehouse transfers
inventory counts
reservations
returns
batch tracking
lot tracking
serial numbers
FIFO cost layers
inventory valuation
cost of goods sold
purchase documents
sales documents
```

FIFO costing is planned but not yet implemented.

---

## 25. Database Design Principles

1. Company-owned records are isolated by company.
2. Operational and financial history remains auditable.
3. Posted documents are not destructively edited.
4. Posted inventory operations are corrected through reversal.
5. Referential integrity is enforced with foreign keys.
6. Important uniqueness rules exist at database level.
7. Accounting periods control dated posting operations.
8. Account codes are unique within a company.
9. Product SKUs are unique within a company.
10. Warehouse names are unique within a company.
11. Inventory history is stored in `stock_ledger`.
12. Current inventory is stored in `stock_balances`.
13. `StockBalance` must reconcile with `StockLedger`.
14. Inventory posting uses PostgreSQL row-level locks.
15. Posting and reversal are atomic.
16. Failed operations roll back completely.
17. Decimal database types are used for quantities and prices.
18. Schema changes are managed through Alembic.
19. Application validation does not replace important database constraints.
20. Future accounting entries must obey double-entry accounting.