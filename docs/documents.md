# Documents and Posting Engine

## 1. Overview

Documents are the operational foundation of the ERP.

A document represents a business operation and may create movements in one or more ERP registers.

The current implementation supports the warehouse posting contour.

Current document types:

```text
receipt
issue
adjustment
```

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
       │
       ├── product_id
       ├── warehouse_id
       ├── quantity
       └── price
```

---

## 3. Document Statuses

Current statuses:

```text
draft
posted
reversed
cancelled
```

`cancelled` is reserved for future functionality.

Current operational lifecycle:

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

## 4. Draft Documents

A draft document does not affect inventory.

Example:

```text
Document
status = DRAFT
quantity = 10
```

At this stage:

```text
StockLedger movement = 0
StockBalance change = 0
```

Draft documents may be edited or deleted.

---

## 5. Creating Documents

Endpoint:

```text
POST /api/v1/companies/{company_id}/documents
```

Required permission:

```text
documents.create
```

The API validates:

```text
company
document number
product
warehouse
company ownership
active status
```

Document creation and creation of all document lines are atomic.

If one line is invalid:

```text
ROLLBACK
```

and neither the document nor any lines are stored.

---

## 6. Reading Documents

Endpoints:

```text
GET /api/v1/companies/{company_id}/documents

GET /api/v1/companies/{company_id}/documents/{document_id}
```

Required permission:

```text
documents.read
```

The single document endpoint returns its document lines.

---

## 7. Editing Draft Documents

Endpoint:

```text
PATCH /api/v1/companies/{company_id}/documents/{document_id}
```

Required permission:

```text
documents.update
```

Only:

```text
DRAFT
```

documents may be edited.

Attempting to edit a posted document returns:

```text
409 Conflict
```

Document-level row locking is used during editing.

---

## 8. Deleting Draft Documents

Endpoint:

```text
DELETE /api/v1/companies/{company_id}/documents/{document_id}
```

Required permission:

```text
documents.delete
```

Only draft documents may be physically deleted.

Their document lines are deleted through:

```text
ON DELETE CASCADE
```

Posted documents cannot be deleted.

---

## 9. Posting Documents

Endpoint:

```text
POST /api/v1/companies/{company_id}/documents/{document_id}/post
```

Required permission:

```text
documents.approve
```

Posting converts a draft document into operational stock movements.

---

## 10. Posting Transaction

Posting follows this flow:

```text
Document
   │
   ▼
Lock Document
   │
   ▼
Check DRAFT Status
   │
   ▼
Check Accounting Period
   │
   ▼
Validate Product
   │
   ▼
Validate Warehouse
   │
   ▼
Validate Quantity
   │
   ▼
Calculate Stock Delta
   │
   ▼
Lock StockBalance
   │
   ▼
Validate New Balance
   │
   ▼
Update StockBalance
   │
   ▼
Create StockLedger
   │
   ▼
Document = POSTED
   │
   ▼
COMMIT
```

If anything fails:

```text
ROLLBACK
```

---

## 11. Atomicity

Posting is atomic.

Example:

```text
Document with 10 lines

line 1 → OK
line 2 → OK
line 3 → OK
...
line 10 → ERROR
```

Result:

```text
0 new StockLedger movements
0 StockBalance changes
Document remains DRAFT
```

A partially posted document is not allowed.

---

## 12. Receipt

Example:

```text
RECEIPT 10
```

Creates:

```text
StockLedger quantity = +10
movement_type = receipt
```

and increases:

```text
StockBalance +10
```

---

## 13. Issue

Example:

```text
ISSUE 7
```

Creates:

```text
StockLedger quantity = -7
movement_type = issue
```

Before posting, the ERP validates available stock.

Negative resulting stock is rejected.

---

## 14. Adjustment

An adjustment may be positive or negative.

Examples:

```text
+5
-3
```

A zero adjustment is not allowed.

Negative adjustments are rejected if they would create negative stock.

---

## 15. Grouped Quantity Validation

Multiple lines using the same:

```text
product
+
warehouse
```

are processed as one combined stock delta.

Example:

```text
Available = 3

Line 1 = 2
Line 2 = 2
```

Total requirement:

```text
4
```

Therefore posting is rejected.

This prevents multiple lines from independently consuming the same available quantity.

---

## 16. Stock Ledger

`stock_ledger` stores historical movements.

Example:

```text
receipt   +10
issue      -7
```

The ledger represents the history of inventory operations.

---

## 17. Stock Balance

`stock_balances` stores the current quantity for:

```text
company
+
product
+
warehouse
```

Example:

```text
receipt +10
issue    -7

StockBalance = 3
```

The expected invariant is:

```text
StockBalance
=
SUM(StockLedger)
```

---

## 18. Concurrency Control

The ERP uses PostgreSQL:

```text
SELECT ... FOR UPDATE
```

to lock the relevant stock balance row.

Example:

```text
Balance = 8

User A → ISSUE 6
User B → ISSUE 6
```

One transaction obtains the lock first.

After it commits:

```text
Balance = 2
```

The second transaction then reads `2` and rejects another issue of `6`.

This prevents concurrent overselling.

---

## 19. Posting the Same Document Twice

A posted document cannot be posted again.

Attempting to post it returns:

```text
409 Conflict
```

No additional stock movement is created.

---

## 20. Reversal

Endpoint:

```text
POST /api/v1/companies/{company_id}/documents/{document_id}/reverse
```

Required permission:

```text
documents.reverse
```

Only a posted document may be reversed.

---

## 21. Reversal Principle

A posted document is not deleted.

Its movements are reversed using new opposite movements.

Example:

```text
Original ISSUE
-6

Reversal
+6
```

The ledger then contains:

```text
-6 issue
+6 reversal
```

The original history remains auditable.

---

## 22. Reversal Accounting Period

Reversal has its own:

```text
reversal_date
```

The ERP checks that this date belongs to an open accounting period.

This allows a document posted in one period to be reversed in another valid open period.

---

## 23. Reversal Stock Validation

Reversal must not result in negative inventory.

Example:

```text
Receipt +10
Issue    -7

Current balance = 3
```

Attempting to reverse the original receipt requires:

```text
-10
```

which would result in:

```text
-7
```

Therefore the reversal is rejected.

---

## 24. Reversal Metadata

When reversal succeeds:

```text
status = reversed
reversed_at = timestamp
reversed_by = user_id
```

This records who performed the correction and when.

---

## 25. Repeated Reversal

A document with status:

```text
reversed
```

cannot be reversed again.

A second reversal attempt returns:

```text
409 Conflict
```

---

## 26. Products API

Current endpoints:

```text
GET   /api/v1/companies/{company_id}/products
POST  /api/v1/companies/{company_id}/products

GET   /api/v1/companies/{company_id}/products/{product_id}
PATCH /api/v1/companies/{company_id}/products/{product_id}
```

Products are company-scoped.

Physical deletion is currently avoided.

Products are deactivated using:

```text
is_active = false
```

---

## 27. Warehouses API

Current endpoints:

```text
GET   /api/v1/companies/{company_id}/warehouses
POST  /api/v1/companies/{company_id}/warehouses

GET   /api/v1/companies/{company_id}/warehouses/{warehouse_id}
PATCH /api/v1/companies/{company_id}/warehouses/{warehouse_id}
```

Warehouses are company-scoped.

Physical deletion is currently avoided.

Warehouses are deactivated using:

```text
is_active = false
```

---

## 28. Current Documents v1 Status

Implemented:

```text
Document
DocumentLine

Receipt
Issue
Adjustment

Draft creation
Draft editing
Draft deletion

Document listing
Document details

Posting
Rollback

StockLedger
StockBalance

Negative stock protection
Grouped stock validation

PostgreSQL concurrency locking

Document reversal
Reversal rollback
Reversal metadata

Products API
Warehouses API

Company-scoped RBAC
```

---

## 29. Planned Next Stage

The next major stage is accounting posting.

Future architecture:

```text
Document
   │
   ├── StockLedger
   ├── StockBalance
   │
   └── JournalEntry
          │
          └── JournalEntryLine
                 │
                 ├── Debit
                 └── Credit
```

Every accounting posting must satisfy:

```text
SUM(Debit) = SUM(Credit)
```

Future posting will also integrate tax and management registers.