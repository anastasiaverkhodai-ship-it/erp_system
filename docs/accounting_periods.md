# Accounting Periods

## 1. Overview

Accounting periods define the time intervals in which accounting operations may be recorded.

Each accounting period belongs to a specific company.

Example:

```text
Company A

2026
├── January
├── February
├── March
├── ...
└── December
```

Accounting periods are an important control mechanism in the ERP because financial transactions must not be freely modified after a period has been closed.

---

## 2. Database Model

Accounting periods are stored in:

```text
accounting_periods
```

Main fields:

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

Each period belongs to:

```text
companies.id
```

through:

```text
company_id
```

---

## 3. Unique Period Rule

A company cannot have two accounting periods for the same year and month.

The database enforces:

```text
UNIQUE(company_id, year, month)
```

For example:

```text
Company 1 + August 2026 → allowed

Company 1 + August 2026
Company 1 + August 2026 → not allowed

Company 2 + August 2026 → allowed
```

---

## 4. Month Validation

The database enforces:

```text
1 <= month <= 12
```

This prevents invalid periods such as:

```text
month = 0
month = 13
month = 25
```

The API also validates the month before the database operation.

---

## 5. Period Dates

When a monthly accounting period is created, the ERP automatically calculates its start and end dates.

Example:

```text
year  = 2026
month = 8
```

becomes:

```text
start_date = 2026-08-01
end_date   = 2026-08-31
```

The user does not manually determine the last day of the month.

---

## 6. Period Status

The current implementation uses:

```text
open
closed
```

### Open

```text
status = open
is_locked = false
```

Accounting operations may be performed for dates inside this period.

### Closed

```text
status = closed
is_locked = true
```

Normal financial modifications for dates inside this period must be rejected.

---

## 7. Closing a Period

Closing a period changes:

```text
status:
open → closed

is_locked:
false → true

closed_at:
null → timestamp
```

Conceptually:

```text
Open Period
    │
    ▼
Close
    │
    ▼
Closed + Locked
```

The closing timestamp is stored for auditability.

---

## 8. Reopening a Period

Authorized users may currently reopen a period.

The operation changes:

```text
status:
closed → open

is_locked:
true → false

closed_at:
timestamp → null
```

Reopening is a sensitive accounting operation.

In future versions, it should be recorded in the audit log.

---

## 9. Period Validation Service

Reusable business logic is located in:

```text
app/services/accounting_period_service.py
```

The main validation function is:

```text
ensure_period_open()
```

It receives:

```text
company_id
operation_date
database session
```

and determines whether the operation may proceed.

Conceptual flow:

```text
Financial Operation
        │
        ▼
operation_date
        │
        ▼
Find Accounting Period
        │
        ├── Not found → Reject
        │
        ▼
Check status
        │
        ├── Closed → Reject
        │
        ▼
Check lock
        │
        ├── Locked → Reject
        │
        ▼
Allow operation
```

---

## 10. Missing Period

If no accounting period exists for the requested operation date, the ERP rejects the operation.

Example:

```text
Document date:
2026-09-15

Existing periods:
2026-08 only
```

Result:

```text
Accounting period does not exist for this date
```

This prevents financial operations from being posted into uncontrolled periods.

---

## 11. Closed Period

Example:

```text
Document date:
2026-08-15

August 2026:
status = closed
is_locked = true
```

The operation must be rejected.

Conceptually:

```text
POST document
      │
      ▼
ensure_period_open()
      │
      ▼
August 2026 = CLOSED
      │
      ▼
409 Conflict
```

---

## 12. Company Isolation

Accounting periods are company-specific.

Example:

```text
Company A
August 2026 → closed

Company B
August 2026 → open
```

Company A being closed must not prevent Company B from operating in the same calendar month.

All period checks therefore include:

```text
company_id
```

---

## 13. Permissions

Current accounting-period permissions:

```text
accounting.periods.read
accounting.periods.manage
```

`read` allows users to view periods.

`manage` controls sensitive operations such as:

```text
create
close
reopen
```

Permissions are checked in the context of the requested company.

---

## 14. API

Current accounting-period API is company-scoped.

Conceptually:

```text
GET
/api/v1/companies/{company_id}/accounting-periods/

POST
/api/v1/companies/{company_id}/accounting-periods/

PATCH
/api/v1/companies/{company_id}/accounting-periods/{period_id}/close

PATCH
/api/v1/companies/{company_id}/accounting-periods/{period_id}/reopen
```

---

## 15. Integration with Journal Entries

Future journal posting will use accounting-period validation.

Example:

```text
Journal Entry
      │
      ▼
Posting Request
      │
      ▼
ensure_period_open()
      │
      ├── Closed → Reject
      │
      ▼
Validate Debit = Credit
      │
      ▼
Post Entry
```

This means a balanced journal entry still cannot be posted into a closed period.

---

## 16. Integration with Business Documents

The same rule will later apply to:

```text
Sales invoices
Purchase invoices
Goods receipts
Goods issues
Warehouse adjustments
Bank transactions
Cash transactions
VAT documents
Manual journal entries
```

A business document that creates financial movements must respect the accounting period.

---

## 17. Editing Posted History

The long-term architecture should avoid directly rewriting posted financial history.

Instead, corrections should normally use mechanisms such as:

```text
reversal
correcting document
adjustment entry
```

This preserves the audit trail.

Closing accounting periods is one part of enforcing this principle.

---

## 18. Future Period Controls

Possible future improvements include:

```text
Closing reason
Closed by user
Reopened by user
Reopening reason
Approval workflow
Year-end closing
Temporary lock
Tax-period lock
Management-accounting lock
Audit events
```

Because the ERP is intended to support multiple accounting contours, future versions may distinguish between financial, tax and management closing rules.

---

## 19. Business Rules

Accounting periods currently follow these core rules:

1. Every period belongs to a company.
2. A company may only have one period for a given year and month.
3. Month must be between 1 and 12.
4. Period dates are generated automatically.
5. Financial operations require an existing period.
6. Financial operations require an open period.
7. Closed periods are locked.
8. Company permissions control period management.
9. Reopening is treated as a privileged operation.
10. Future financial modules must use the common period-validation service.