# Chart of Accounts

## 1. Overview

The Chart of Accounts defines the accounting accounts used by each company in the ERP System.

Accounts will be used by the double-entry accounting system for:

- Debit entries
- Credit entries
- General Ledger
- Trial Balance
- Financial reports
- Accounting documents
- Future tax and management accounting integrations

Each company maintains its own Chart of Accounts.

---

## 2. Database Model

Accounts are stored in:

```text
accounts
```

Main fields:

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

Each account belongs to a company through:

```text
company_id → companies.id
```

---

## 3. Company-Specific Accounts

Accounts are isolated by company.

Example:

```text
Company A
├── 281
├── 361
└── 631

Company B
├── 281
├── 361
└── 631
```

The same account code may therefore exist in different companies.

However, it cannot be duplicated inside the same company.

---

## 4. Account Code

`code` represents the accounting account number.

Examples:

```text
28
281
282
361
631
```

The database enforces:

```text
UNIQUE(company_id, code)
```

Therefore:

```text
Company 1 + 281 → allowed
Company 1 + 281 → duplicate, rejected

Company 2 + 281 → allowed
```

---

## 5. Account Name

`name` contains the human-readable account name.

Example:

```text
28  Товари
281 Товари на складі
282 Товари в торгівлі
361 Розрахунки з вітчизняними покупцями
631 Розрахунки з вітчизняними постачальниками
```

The ERP may later support additional localization or standardized account templates.

---

## 6. Account Types

The current model stores:

```text
account_type
```

Current conceptual types include:

```text
asset
liability
equity
income
expense
```

This field will later help reporting and accounting logic determine the economic nature of an account.

The account-type model may become more detailed as the accounting subsystem develops.

---

## 7. Hierarchical Accounts

Accounts support parent-child relationships.

The field:

```text
parent_id
```

references:

```text
accounts.id
```

Example:

```text
28 Товари
│
├── 281 Товари на складі
└── 282 Товари в торгівлі
```

This allows the ERP to represent both higher-level accounts and subaccounts.

---

## 8. Parent Account Validation

When assigning a parent account, the API verifies that the parent belongs to the same company.

Invalid:

```text
Company A
Account 281
   │
   └── parent → Account from Company B
```

Valid:

```text
Company A
28
│
└── 281
```

An account is also prevented from being its own direct parent.

Future validation should additionally prevent deeper circular relationships.

For example, this must never become possible:

```text
28
└── 281
    └── 28
```

---

## 9. Active Accounts

Accounts contain:

```text
is_active
```

An inactive account remains in the database but should not normally be available for new accounting operations.

This is preferable to deleting accounts that may already be referenced by historical accounting entries.

Example:

```text
281
is_active = false
```

Historical entries remain valid, but new posting to the account can later be blocked.

---

## 10. Permissions

Current permissions:

```text
accounts.read
accounts.create
accounts.update
```

They are checked in the context of a company.

Typical access:

```text
Admin       → read/create/update
Director    → read
Accountant  → read/create/update
Manager     → read
Seller      → no access
```

The exact permission configuration remains controlled by RBAC seed data.

---

## 11. API

Current company-scoped endpoints include:

```text
GET
/api/v1/companies/{company_id}/accounts/

POST
/api/v1/companies/{company_id}/accounts/

PATCH
/api/v1/companies/{company_id}/accounts/{account_id}
```

The API supports:

```text
list accounts
create account
update account
activate/deactivate account
assign parent account
```

---

## 12. Example Account

Example request:

```json
{
  "code": "28",
  "name": "Товари",
  "account_type": "asset",
  "parent_id": null
}
```

Example child:

```json
{
  "code": "281",
  "name": "Товари на складі",
  "account_type": "asset",
  "parent_id": 1
}
```

Conceptually:

```text
28
└── 281
```

---

## 13. Integration with Double-Entry Accounting

The Chart of Accounts will become the foundation of journal entries.

Example transaction:

```text
Purchase of inventory

Debit  281
Credit 631
```

Future journal lines will reference accounts from this table.

Conceptually:

```text
Journal Entry
      │
      ▼
Journal Entry Line
      │
      ▼
Account
```

---

## 14. Debit and Credit

Accounts themselves do not store individual debit and credit transactions.

Instead, journal-entry lines will reference accounts.

Example:

```text
Journal Entry #100

Debit:
281 → 10,000.00

Credit:
631 → 10,000.00
```

The posting engine must verify:

```text
Total Debit = Total Credit
```

before the transaction is posted.

---

## 15. Balances

Account balances should not be manually editable fields.

Instead:

```text
Account Balance
      =
Opening Balance
      +
Posted Debit/Credit Movements
```

The General Ledger will become the source for calculating accounting balances.

This avoids inconsistencies caused by manually changing stored balances.

---

## 16. General Ledger Integration

Future architecture:

```text
Account
   │
   ▼
Journal Entry Lines
   │
   ▼
General Ledger
   │
   ▼
Account Turnover
   │
   ▼
Account Balance
```

This will allow reports to calculate movements and balances for any accounting period.

---

## 17. Trial Balance Integration

The Chart of Accounts will also be used by the Trial Balance.

Conceptually:

```text
Account | Opening | Debit | Credit | Closing
------------------------------------------------
281     | ...     | ...   | ...    | ...
361     | ...     | ...   | ...    | ...
631     | ...     | ...   | ...    | ...
```

The Trial Balance will be calculated from posted accounting movements.

---

## 18. Ukrainian Accounting Support

The ERP is intended to support accounting workflows used by Ukrainian businesses.

The architecture should therefore be capable of representing account structures such as:

```text
28
281
282

36
361
362

63
631
632
```

However, the database architecture should not hard-code one company's entire Chart of Accounts.

Future functionality may provide a standard Ukrainian Chart of Accounts template that can be imported when a company is created.

---

## 19. Future Analytical Accounting

A simple account number is not sufficient for every ERP accounting operation.

Future accounting entries may include analytical dimensions such as:

```text
Counterparty
Contract
Product
Warehouse
Project
Department
Employee
Currency
```

Example:

```text
Account:      361
Counterparty: Customer A
Contract:     Contract 2026-15
Currency:     UAH
```

These dimensions should be implemented separately rather than creating excessive account codes.

---

## 20. Future Improvements

Planned Chart of Accounts improvements include:

```text
Standard Ukrainian account template
Account categories
Currency settings
Off-balance accounts
Analytical dimensions
Account search
Tree API
Bulk account import
Account usage validation
Circular hierarchy prevention
Opening balances
Account history
Audit logging
```

---

## 21. Business Rules

The Chart of Accounts follows these principles:

1. Every account belongs to a company.
2. Account codes are unique inside a company.
3. Different companies may use the same account code.
4. Accounts may have parent accounts.
5. Parent accounts must belong to the same company.
6. An account cannot be its own direct parent.
7. Inactive accounts should remain available for historical records.
8. New postings should eventually be blocked for inactive accounts.
9. Account balances should be derived from accounting movements.
10. Journal entries must reference valid company accounts.
11. Accounting entries must satisfy debit equals credit.
12. Historical accounts should not be deleted when they are referenced by posted transactions.