# RBAC Architecture

## 1. Overview

The ERP System uses Role-Based Access Control.

Authorization is enforced by the backend.

The frontend may hide unavailable actions for usability, but backend authorization remains the security boundary.

---

## 2. Current Standard Roles

```text
admin
director
accountant
manager
seller
```

---

## 3. Main RBAC Tables

```text
roles
permissions
user_roles
role_permissions
user_companies
user_company_roles
```

`user_company_roles` allows one user to have different roles in different companies.

---

## 4. Current Permission Groups

### Users

```text
users.read
users.create
users.update
```

### Companies

```text
companies.create
companies.read
companies.update
```

### Products

```text
products.read
products.create
products.update
```

### Warehouses

```text
warehouse.read
warehouse.create
warehouse.update
```

### Warehouse Documents

```text
documents.read
documents.create
documents.update
documents.delete
documents.approve
documents.reverse
```

### Reports

```text
reports.read
```

### Accounting Periods

```text
accounting.periods.read
accounting.periods.manage
```

### Chart of Accounts

```text
accounts.read
accounts.create
accounts.update
```

### Journal Entries

```text
journal_entries.read
journal_entries.create
journal_entries.update
journal_entries.delete
journal_entries.approve
journal_entries.reverse
```

---

## 5. Journal Entry Permission Model

Permissions are deliberately separated by action.

```text
journal_entries.read
```

allows reading journal entries.

```text
journal_entries.create
```

allows creation of draft entries.

```text
journal_entries.update
```

allows editing draft entries.

```text
journal_entries.delete
```

allows deleting draft entries.

```text
journal_entries.approve
```

allows posting a balanced draft entry.

```text
journal_entries.reverse
```

allows reversal of a posted entry.

---

## 6. Journal Entry Role Matrix

| Permission | Admin | Director | Accountant | Manager | Seller |
|---|---|---|---|---|---|
| journal_entries.read | ✅ | ✅ | ✅ | — | — |
| journal_entries.create | ✅ | — | ✅ | — | — |
| journal_entries.update | ✅ | — | ✅ | — | — |
| journal_entries.delete | ✅ | — | ✅ | — | — |
| journal_entries.approve | ✅ | ✅ | ✅ | — | — |
| journal_entries.reverse | ✅ | ✅ | ✅ | — | — |

The source of truth for seeded role permissions is:

```text
scripts/seed_role_permissions.py
```

---

## 7. Journal Lifecycle Security

### DRAFT

With the appropriate permissions:

```text
GET     allowed
PATCH   allowed
DELETE  allowed
POST    allowed
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
GET              allowed
PATCH            rejected
DELETE           rejected
second reversal  rejected
```

---

## 8. Warehouse Document Permissions

Warehouse document permissions remain separate:

```text
documents.read
documents.create
documents.update
documents.delete
documents.approve
documents.reverse
```

Warehouse posting permission does not automatically grant accounting posting permission.

This separation allows future approval workflows to distinguish:

```text
warehouse operation
```

from:

```text
accounting approval
```

---

## 9. Company Permission Enforcement

Company-scoped authorization uses:

```text
require_company_permission(...)
```

Conceptually:

```text
Request
   │
   ▼
Authenticated User
   │
   ▼
UserCompanyRole
   │
   ▼
Role
   │
   ▼
RolePermission
   │
   ▼
Permission
```

The relevant company is part of the authorization decision.

---

## 10. Global Permission Enforcement

Global/system-level authorization uses:

```text
require_global_permission(...)
```

This is separate from company-scoped business permissions.

---

## 11. Security Principle

The frontend is never trusted as the sole authorization mechanism.

For example, hiding a `Reverse` button is not sufficient.

The backend must still validate:

```text
journal_entries.reverse
```

or:

```text
documents.reverse
```

before performing the operation.

---

## 12. Future RBAC Extensions

Future versions may include:

```text
custom roles
role management UI
permission management UI
warehouse-specific access
accounting-area restrictions
approval thresholds
document amount limits
department restrictions
auditor roles
cashier roles
```