# Documents and Warehouse Posting Engine

## 1. Overview

Warehouse documents are the operational foundation of inventory movement.

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

---

## 2. Document Structure

```text
Document
│
├── company_id
├── number
├── document_type
├── document_date
├── status
├── created_by
├── created_at
├── posted_at
├── reversed_at
├── reversed_by
│
└── DocumentLine[]
       ├── product_id
       ├── warehouse_id
       ├── quantity
       └── price
```

---

## 3. Draft Lifecycle

A draft does not affect stock.

```text
DRAFT
├── editable
├── deletable
└── no StockLedger movement
```

---

## 4. Posting

Endpoint:

```text
POST /api/v1/companies/{company_id}/documents/{document_id}/post
```

Posting performs:

```text
Lock Document
      │
      ▼
Validate DRAFT
      │
      ▼
Check Accounting Period
      │
      ▼
Validate Products
      │
      ▼
Validate Warehouses
      │
      ▼
Calculate Combined Stock Deltas
      │
      ▼
Lock StockBalance Rows
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
Document = POSTED
```

Posting is atomic.

Any failure causes rollback.

---

## 5. Stock Ledger

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
Receipt +10
Issue    -7
------------
Balance   3
```

---

## 6. Stock Balance

`stock_balances` stores current operational inventory.

One row represents:

```text
company
+
product
+
warehouse
```

Expected invariant:

```text
StockBalance.quantity
=
SUM(StockLedger.quantity)
```

---

## 7. Negative Stock Protection

Issues and negative adjustments are rejected if the resulting stock would be negative.

Multiple lines affecting the same product and warehouse are aggregated before validation.

Example:

```text
Available = 3

Line 1 issue = 2
Line 2 issue = 2

Combined issue = 4
```

Result:

```text
REJECT
```

---

## 8. Concurrency

Posting uses PostgreSQL:

```text
SELECT ... FOR UPDATE
```

on StockBalance rows.

This prevents concurrent overselling.

Locks for multiple balance rows are acquired in deterministic order where possible.

---

## 9. Reversal

Endpoint:

```text
POST /api/v1/companies/{company_id}/documents/{document_id}/reverse
```

Reversal creates opposite StockLedger movements.

Example:

```text
Original issue:
-6

Reversal:
+6
```

Original ledger movements remain in history.

The document becomes:

```text
status = reversed
reversed_at = timestamp
reversed_by = user_id
```

---

## 10. Reversal Stock Validation

Reversal cannot produce negative stock.

Example:

```text
Receipt +10
Issue    -7

Current = 3
```

Reversing the receipt requires:

```text
-10
```

which would produce:

```text
-7
```

Therefore reversal is rejected.

---

## 11. Current Warehouse API

```text
GET
/api/v1/companies/{company_id}/documents

POST
/api/v1/companies/{company_id}/documents

GET
/api/v1/companies/{company_id}/documents/{document_id}

PATCH
/api/v1/companies/{company_id}/documents/{document_id}

DELETE
/api/v1/companies/{company_id}/documents/{document_id}

POST
/api/v1/companies/{company_id}/documents/{document_id}/post

POST
/api/v1/companies/{company_id}/documents/{document_id}/reverse
```

---

# Accounting Relationship

## 12. Journal Entries Now Exist

The ERP now has a separate accounting posting engine:

```text
JournalEntry
└── JournalEntryLine
```

with:

```text
DRAFT
  ↓
POSTED
  ↓
REVERSED
```

The accounting engine validates:

```text
Debit = Credit
company ownership
active accounts for new posting
accounting periods
reversal history
```

---

## 13. Warehouse Documents Do Not Yet Automatically Create Journal Entries

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

Automatic:

```text
Document → JournalEntry
```

is not yet implemented.

This is intentional.

A generic warehouse receipt does not always imply:

```text
Dr 281
Cr 631
```

because the counter-account depends on the business operation.

Possible examples include:

```text
631 suppliers
685 other creditors
372 accountable persons
46 unpaid capital
719 other operating income
```

depending on the actual economic substance.

Therefore automatic accounting posting will be introduced together with richer business document types and posting rules.

---

## 14. Future Integrated Posting

Future architecture:

```text
Business Document
       │
       ▼
Posting Rules
       │
       ├── StockLedger
       ├── StockBalance
       │
       ├── JournalEntry
       ├── JournalEntryLine
       │
       ├── VAT Registers
       └── Management Registers
```

All generated movements for one business operation should eventually participate in one consistent transactional process.