# Accounting Posting Engine

## 1. Overview

Accounting Posting v1 provides the first double-entry accounting engine of the ERP.

Main entities:

```text
JournalEntry
JournalEntryLine
```

The accounting engine currently supports manual company-scoped journal entries.

Automatic creation of JournalEntry records from warehouse documents is planned for a later stage.

---

## 2. JournalEntry

Table:

```text
journal_entries
```

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

---

## 3. JournalEntryLine

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

---

## 4. One-Sided Journal Lines

Each line must contain exactly one accounting side.

Valid:

```text
Debit = 1000
Credit = 0
```

Valid:

```text
Debit = 0
Credit = 1000
```

Invalid:

```text
Debit = 1000
Credit = 1000
```

Invalid:

```text
Debit = 0
Credit = 0
```

This rule is enforced by both application validation and database constraints.

---

## 5. Draft Journal Entries

A draft may temporarily be unbalanced.

Example:

```text
Debit  = 1000
Credit = 900
```

This allows an accountant to save unfinished work.

Draft journal entries may be:

```text
created
read
updated
deleted
```

---

## 6. Posting

Posting endpoint:

```text
POST /api/v1/companies/{company_id}/journal-entries/{journal_entry_id}/post
```

Required permission:

```text
journal_entries.approve
```

Posting flow:

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
Calculate Debit Total
       │
       ▼
Calculate Credit Total
       │
       ▼
Require Debit = Credit
       │
       ▼
Validate Accounts
       │
       ▼
JournalEntry = POSTED
       │
       ▼
posted_at = timestamp
```

---

## 7. Double-Entry Rule

Posting requires:

```text
SUM(Debit) = SUM(Credit)
```

and:

```text
SUM(Debit) > 0
```

Example:

```text
Dr 281   1000.00
Cr 631   1000.00
```

Result:

```text
POSTED
```

Example:

```text
Dr 281   1000.00
Cr 631    900.00
```

Result:

```text
REJECT
```

The JournalEntry remains:

```text
DRAFT
```

---

## 8. Account Validation

For new posting, every account must:

```text
exist
belong to JournalEntry.company_id
be active
```

Example:

```text
JournalEntry.company_id = 1

Debit account company_id = 2
```

Result:

```text
REJECT
```

This protects company isolation.

---

## 9. Inactive Account Protection

A new posting cannot use an inactive account.

Example:

```text
Account 631
is_active = false
```

Result:

```text
POSTING REJECTED
```

---

## 10. Accounting Period Validation

Posting checks:

```text
entry_date
```

against the accounting period service.

The relevant accounting period must allow posting.

---

## 11. Posting Atomicity

`post_journal_entry()` does not commit directly.

The caller controls:

```text
COMMIT
```

or:

```text
ROLLBACK
```

This allows the accounting service to later participate inside a larger integrated business transaction.

---

## 12. Posting Concurrency

The JournalEntry row is locked with:

```text
SELECT ... FOR UPDATE
```

during posting.

This protects lifecycle transitions from conflicting concurrent operations.

---

# Accounting Reversal

## 13. Reversal Overview

Posted JournalEntry records are corrected using reversal.

The original accounting lines are not rewritten.

Example original:

```text
Dr 281   1000
Cr 631   1000
```

Reversal:

```text
Dr 631   1000
Cr 281   1000
```

---

## 14. Reversal Endpoint

```text
POST /api/v1/companies/{company_id}/journal-entries/{journal_entry_id}/reverse
```

Required permission:

```text
journal_entries.reverse
```

Request:

```json
{
  "reversal_date": "2026-08-18"
}
```

---

## 15. Reversal Result

The original entry becomes:

```text
status = reversed
reversed_at = timestamp
reversed_by = user_id
```

A new JournalEntry is created with:

```text
status = posted
reversal_of_id = original.id
```

The new lines swap debit and credit.

---

## 16. Reversal Link

Example:

```text
JournalEntry #100
status = reversed
```

and:

```text
JournalEntry #101
status = posted
reversal_of_id = 100
```

Database constraint:

```text
UNIQUE(reversal_of_id)
```

prevents two reversal entries from referencing the same original.

---

## 17. Reversal Accounting Period

The reversal has its own:

```text
reversal_date
```

This date is validated against the accounting period service.

---

## 18. Historical Accounts

A reversal does not require accounts to remain active.

This is intentional.

Example:

```text
2026-01:
posting uses Account 631

2026-07:
Account 631 becomes inactive

2026-08:
old posting must be reversed
```

The system may still create the reversal as long as the historical account belongs to the correct company.

---

## 19. Double Reversal Protection

An original JournalEntry can only have one reversal.

After reversal:

```text
Original status = reversed
```

A second reversal attempt is rejected.

The current service also rejects attempts to reverse a reversal JournalEntry.

---

# API

## 20. Current Endpoints

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

## 21. Lifecycle Rules

```text
DRAFT
├── read
├── update
├── delete
└── post

POSTED
├── read
├── reverse
├── no update
└── no delete

REVERSED
├── read
├── no update
├── no delete
└── no second reversal
```

---

## 22. Permissions

```text
journal_entries.read
journal_entries.create
journal_entries.update
journal_entries.delete
journal_entries.approve
journal_entries.reverse
```

---

## 23. Current Accounting v1 Capabilities

Implemented:

```text
JournalEntry model
JournalEntryLine model

Draft creation
Draft listing
Draft details
Draft editing
Draft deletion

Double-entry validation
Account company validation
Inactive account validation

Accounting period validation

Posting
Posting row lock

Accounting reversal
Reversal JournalEntry
Reversal metadata
Reversal period validation
Double reversal protection

Company-scoped RBAC
FastAPI endpoints
Swagger-tested lifecycle
```

---

## 24. Current Limitation

Warehouse documents do not yet automatically create accounting JournalEntry records.

Current architecture:

```text
Warehouse Document
   │
   ├── StockLedger
   └── StockBalance
```

and separately:

```text
JournalEntry
   │
   └── JournalEntryLine
```

The next major accounting step is to define business-document accounting rules before connecting these two posting contours.

---

## 25. Future Integrated Accounting

Future architecture:

```text
Business Document
       │
       ▼
Posting Rules
       │
       ├── Stock Ledger
       ├── Stock Balance
       │
       ├── Journal Entry
       ├── General Ledger
       │
       ├── VAT Registers
       ├── Tax Registers
       └── Management Registers
```

Future accounting development will include:

```text
purchase accounting
sales accounting
cost of goods sold
FIFO valuation
VAT
bank transactions
cash transactions
receivables
payables
financial reporting
tax reporting
```