# Database Architecture

## 1. Overview

The ERP System uses PostgreSQL with asynchronous SQLAlchemy 2.0 sessions.

Database schema changes are managed through Alembic migrations.

Core architectural goals include:

- multi-company isolation
- referential integrity
- auditable operational history
- transactional posting
- warehouse concurrency protection
- double-entry accounting
- reversal instead of destructive modification of posted records

---

## 2. Current Major Tables

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

journal_entries
journal_entry_lines

audit_logs
```

---

## 3. High-Level Structure

```text
Company
│
├── AccountingPeriod
├── Account
│
├── Product
├── Warehouse
│
├── Document
│     └── DocumentLine
│            └── StockLedger
│
├── StockBalance
│
└── JournalEntry
       └── JournalEntryLine
              └── Account
```

Company-owned operational data is isolated by `company_id`.

---

## 4. Products

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

Constraint:

```text
UNIQUE(company_id, sku)
```

---

## 5. Warehouses

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

Constraint:

```text
UNIQUE(company_id, name)
```

---

## 6. Documents

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

Constraint:

```text
UNIQUE(company_id, number)
```

Current types:

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

---

## 7. Document Lines

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

Quantities and prices use fixed precision:

```text
NUMERIC(18,4)
```

---

## 8. Stock Ledger

Table:

```text
stock_ledger
```

Stores historical inventory movements.

Current movement types:

```text
receipt
issue
adjustment
reversal
```

Operational stock history is preserved rather than rewritten.

---

## 9. Stock Balances

Table:

```text
stock_balances
```

Stores current quantity for:

```text
company_id
product_id
warehouse_id
```

Constraint:

```text
UNIQUE(
    company_id,
    product_id,
    warehouse_id
)
```

Expected invariant:

```text
StockBalance.quantity
=
SUM(StockLedger.quantity)
```

for the same company, product and warehouse.

StockBalance rows are also used for PostgreSQL row-level locking during inventory posting.

---

# Accounting Database

## 10. Journal Entries

Table:

```text
journal_entries
```

Represents an accounting transaction header.

Important fields:

```text
id
company_id
document_id
entry_date
description
status
created_by
created_at
posted_at
reversed_at
reversed_by
reversal_of_id
```

Current statuses:

```text
draft
posted
reversed
```

`document_id` is nullable.

This allows:

```text
manual journal entries
```

as well as future:

```text
document-generated journal entries
```

---

## 11. Journal Entry Reversal Relationship

`reversal_of_id` references:

```text
journal_entries.id
```

This creates an explicit relationship:

```text
Original JournalEntry
        │
        ▼
Reversal JournalEntry
```

Constraint:

```text
UNIQUE(reversal_of_id)
```

Therefore an original JournalEntry can have at most one reversal JournalEntry.

---

## 12. Journal Entry Lines

Table:

```text
journal_entry_lines
```

Important fields:

```text
id
journal_entry_id
line_no
account_id
debit
credit
description
```

Amounts use:

```text
NUMERIC(18,2)
```

Constraint:

```text
UNIQUE(journal_entry_id, line_no)
```

---

## 13. Debit / Credit Constraints

The database enforces:

```text
debit >= 0
credit >= 0
```

and:

```text
(debit > 0 AND credit = 0)
OR
(credit > 0 AND debit = 0)
```

Therefore a journal line cannot contain:

```text
Debit > 0 AND Credit > 0
```

and cannot contain:

```text
Debit = 0 AND Credit = 0
```

---

## 14. Double-Entry Balance Rule

Balance across the entire JournalEntry is enforced by application posting logic.

A JournalEntry may exist as an unbalanced draft.

It cannot be posted unless:

```text
SUM(debit) = SUM(credit)
```

and:

```text
SUM(debit) > 0
```

Example:

```text
Dr 281   1000.00
Cr 631   1000.00
```

is valid.

Example:

```text
Dr 281   1000.00
Cr 631    900.00
```

is rejected during posting.

---

## 15. Account Company Integrity

Every account used during new accounting posting must:

```text
exist
belong to the same company
be active
```

A JournalEntry belonging to Company 1 cannot be posted using an account belonging to Company 2.

---

## 16. Accounting Period Integrity

Journal posting validates:

```text
JournalEntry.entry_date
```

against the accounting period service.

Reversal validates:

```text
reversal_date
```

independently.

---

## 17. Journal Posting Lifecycle

```text
DRAFT
  │
  │ validation
  ▼
POSTED
```

Posting:

```text
Lock JournalEntry
        │
        ▼
Check DRAFT
        │
        ▼
Check Accounting Period
        │
        ▼
Check Lines
        │
        ▼
Check Debit = Credit
        │
        ▼
Check Accounts
        │
        ▼
status = POSTED
posted_at = timestamp
```

Posting does not commit inside the service.

Transaction ownership remains with the caller.

---

## 18. Accounting Reversal

Accounting reversal preserves the original entry.

Example:

```text
Original:

Dr 281  1000
Cr 631  1000
```

Reversal:

```text
Dr 631  1000
Cr 281  1000
```

The reversal is created as a new posted JournalEntry.

The original becomes:

```text
status = reversed
reversed_at = timestamp
reversed_by = user_id
```

The new reversal contains:

```text
reversal_of_id = original.id
```

---

## 19. Historical Account Handling

New postings require active accounts.

Reversal does not require historical accounts to remain active.

This is necessary so valid historical transactions can still be reversed after an account has been deactivated.

Accounts used during reversal must still belong to the same company.

---

## 20. Warehouse and Accounting Separation

The current ERP contains two posting contours:

```text
Warehouse Posting
        │
        ├── StockLedger
        └── StockBalance
```

and:

```text
Accounting Posting
        │
        ├── JournalEntry
        └── JournalEntryLine
```

They are not yet automatically linked.

A generic warehouse receipt, issue, or adjustment does not necessarily provide enough business context to determine the correct accounting correspondence.

Document-to-accounting rules will be introduced in a later stage.

---

## 21. Audit Logs

Table:

```text
audit_logs
```

Provides a foundation for system auditability.

Automatic audit integration is still planned for later stages.

---

## 22. Core Database Principles

1. Company-owned business data is company-scoped.
2. Historical posted records are not destructively rewritten.
3. Corrections use reversal.
4. Warehouse movements preserve StockLedger history.
5. Current stock is maintained in StockBalance.
6. Inventory posting uses row-level locking.
7. Accounting entries use double-entry accounting.
8. Journal lines enforce one-sided Debit/Credit at database level.
9. Journal posting enforces total Debit = total Credit.
10. Foreign-company accounts cannot be used during posting.
11. New postings require active accounts.
12. Historical reversal remains possible after account deactivation.
13. Accounting periods control posting dates.
14. Posting and reversal are atomic operations.
15. Schema changes are managed through Alembic.