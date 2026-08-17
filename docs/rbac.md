# RBAC Architecture

## 1. Overview

The ERP System uses Role-Based Access Control (RBAC).

Authorization is enforced by the backend.

The client application must never be treated as the security boundary.

The system supports:

```text
Global Roles
Company-Specific Roles
Permissions
```

---

## 2. Main Tables

```text
users
roles
permissions

user_roles
role_permissions

user_companies
user_company_roles
```

---

## 3. Global Roles

Table:

```text
user_roles
```

Global roles are used for system-level permissions.

Example:

```text
companies.create
```

A global administrator may create a new company.

---

## 4. Company Roles

Table:

```text
user_company_roles
```

Company roles apply inside one specific company.

Example:

```text
User
│
├── Company A → accountant
└── Company B → manager
```

The same user can therefore have different permissions in different companies.

---

## 5. Standard Roles

Current standard roles:

```text
admin
director
accountant
manager
seller
```

These roles are an initial ERP configuration.

Additional roles can be added later without redesigning the core business tables.

Possible future roles include:

```text
warehouse_operator
cashier
auditor
senior_accountant
procurement_manager
```

---

## 6. Current Permissions

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

### Documents

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

---

## 7. Current Role Matrix

| Permission | Admin | Director | Accountant | Manager | Seller |
|---|---|---|---|---|---|
| users.read | ✅ | ✅ | — | — | — |
| users.create | ✅ | ✅ | — | — | — |
| users.update | ✅ | ✅ | — | — | — |
| companies.create | ✅ | — | — | — | — |
| companies.read | ✅ | ✅ | ✅ | — | — |
| companies.update | ✅ | ✅ | — | — | — |
| products.read | ✅ | ✅ | — | ✅ | ✅ |
| products.create | ✅ | ✅ | — | ✅ | — |
| products.update | ✅ | ✅ | — | ✅ | — |
| warehouse.read | ✅ | ✅ | — | ✅ | — |
| warehouse.create | ✅ | ✅ | — | ✅ | — |
| warehouse.update | ✅ | ✅ | — | ✅ | — |
| documents.read | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.create | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.update | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.delete | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.approve | ✅ | ✅ | ✅ | — | — |
| documents.reverse | ✅ | ✅ | ✅ | — | — |
| reports.read | ✅ | ✅ | ✅ | — | — |
| accounting.periods.read | ✅ | ✅ | ✅ | — | — |
| accounting.periods.manage | ✅ | — | ✅ | — | — |
| accounts.read | ✅ | ✅ | ✅ | ✅ | — |
| accounts.create | ✅ | — | ✅ | — | — |
| accounts.update | ✅ | — | ✅ | — | — |

This matrix reflects the current seeded configuration.

The source of truth for seeded role permissions is:

```text
scripts/seed_role_permissions.py
```

---

## 8. Document Permissions

Document permissions are separated by operation:

```text
documents.read
documents.create
documents.update
documents.delete
documents.approve
documents.reverse
```

This allows the ERP to distinguish between:

```text
reading documents
creating a draft
editing a draft
deleting a draft
posting a document
reversing a posted document
```

Posting uses:

```text
documents.approve
```

Reversal uses:

```text
documents.reverse
```

---

## 9. Document Lifecycle Security

### DRAFT

A user with appropriate permissions may:

```text
read
update
delete
post
```

### POSTED

A posted document cannot be edited or physically deleted.

A user with:

```text
documents.reverse
```

may reverse it.

### REVERSED

A reversed document remains historical and cannot be reversed again.

---

## 10. Company Permission Enforcement

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

The check includes:

```text
user_id
company_id
role
is_active
permission
```

---

## 11. Global Permission Enforcement

Global permissions use:

```text
require_global_permission(...)
```

For example:

```text
companies.create
```

This is separate from company-specific authorization.

---

## 12. Security Principle

The frontend may hide controls based on permissions for usability.

However, the backend always remains the final authorization authority.

For example:

```text
Frontend hides "Reverse"
```

is not sufficient.

The API must still enforce:

```text
documents.reverse
```

---

## 13. Future RBAC Development

Future ERP versions may support:

```text
custom roles
role management UI
permission management UI
company-specific role templates
more granular accounting permissions
warehouse-specific restrictions
approval limits
document amount limits
department-level access
```

The current RBAC architecture is designed so these additions do not require redesigning the main ERP business tables.