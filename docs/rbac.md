# Role-Based Access Control (RBAC)

## 1. Overview

The ERP System uses Role-Based Access Control (RBAC) to determine what authenticated users are allowed to do.

Authentication and authorization are separate:

```text
Authentication
     │
     ▼
Who is the user?
     │
     ▼
Authorization / RBAC
     │
     ▼
What can the user do?
```

The ERP supports company-specific authorization.

A user may have different roles in different companies.

---

## 2. Authorization Model

The main authorization structure is:

```text
User
 │
 ▼
UserCompanyRole
 │
 ├── Company
 │
 └── Role
      │
      ▼
RolePermission
      │
      ▼
Permission
```

A permission check therefore depends on:

```text
user_id
+
company_id
+
role
+
permission
```

---

## 3. Company-Specific Roles

A user may work with multiple companies.

Example:

```text
User: test@example.com

Company A
└── accountant

Company B
└── manager
```

The user's permissions in Company A do not automatically apply to Company B.

This provides isolation between companies.

---

## 4. Roles

Current system roles:

```text
admin
director
accountant
manager
seller
```

### Admin

System/company administrator with broad access.

Typical responsibilities:

- user management
- company configuration
- product management
- warehouse management
- document management
- accounting configuration
- reporting

### Director

Management-level role with broad visibility and control.

Typical responsibilities:

- company information
- reports
- documents
- operational oversight
- selected accounting information

### Accountant

Responsible for accounting-related operations.

Typical responsibilities:

- accounting periods
- Chart of Accounts
- accounting documents
- reports
- future journal entries
- future tax accounting

### Manager

Responsible primarily for operational processes.

Typical responsibilities:

- products
- warehouses
- business documents
- selected accounting information

### Seller

Limited operational role.

Typical responsibilities:

- viewing products
- reading documents
- creating permitted sales-related documents

---

## 5. Permissions

Permissions use a resource/action naming convention:

```text
resource.action
```

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

This convention makes permissions explicit and extensible.

Future examples may include:

```text
journal.read
journal.create
journal.post
journal.reverse

sales.read
sales.create
sales.post

purchases.read
purchases.create
purchases.post

bank.read
bank.import

vat.read
vat.manage
```

---

## 6. Role-Permission Relationship

Roles and permissions have a many-to-many relationship.

Database structure:

```text
roles
  │
  ▼
role_permissions
  │
  ▼
permissions
```

One role can contain many permissions.

One permission can belong to multiple roles.

---

## 7. Company Role Assignment

Company-specific role assignments are stored in:

```text
user_company_roles
```

Important fields:

```text
user_id
company_id
role_id
is_active
assigned_at
```

The combination identifies which role a user has in a particular company.

---

## 8. Active Role Check

A role assignment can be deactivated using:

```text
is_active = false
```

This allows access to be removed without deleting historical role information.

Permission checks must only use active company-role assignments.

Conceptually:

```text
User
 │
 ▼
Company Role
 │
 ├── is_active = true  → continue
 │
 └── is_active = false → deny
```

---

## 9. Company Permission Check

Company resources use a permission dependency such as:

```python
Depends(
    require_company_permission("accounts.read")
)
```

The permission checker verifies that:

```text
current user
     │
     ▼
has active role
     │
     ▼
inside requested company
     │
     ▼
role contains requested permission
```

If the permission exists:

```text
Request allowed
```

If it does not:

```text
HTTP 403 Forbidden
```

---

## 10. Authentication Failure vs Permission Failure

These cases are intentionally different.

### 401 Unauthorized

Example:

```json
{
  "detail": "Not authenticated"
}
```

Meaning:

```text
No valid authentication credentials were provided.
```

### 403 Forbidden

Example:

```json
{
  "detail": "Permission denied for this company"
}
```

Meaning:

```text
The user is authenticated,
but does not have the required permission.
```

This distinction is important for API behavior.

---

## 11. Current Role Matrix

The current permission model can be summarized approximately as:

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
| documents.read | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.create | ✅ | ✅ | ✅ | ✅ | ✅ |
| documents.approve | ✅ | ✅ | ✅ | — | — |
| reports.read | ✅ | ✅ | ✅ | — | — |
| accounting.periods.read | ✅ | ✅ | ✅ | — | — |
| accounting.periods.manage | ✅ | — | ✅ | — | — |
| accounts.read | ✅ | ✅ | ✅ | ✅ | — |
| accounts.create | ✅ | — | ✅ | — | — |
| accounts.update | ✅ | — | ✅ | — | — |

This table reflects the current permission assignments defined in:

```text
scripts/seed_role_permissions.py

---

## 12. RBAC Seed Scripts

RBAC initialization is handled through scripts.

Examples:

```text
scripts/seed_rbac.py
scripts/seed_role_permissions.py
scripts/check_rbac.py
scripts/check_role_permissions.py
```

Typical workflow:

```bash
python -m scripts.seed_rbac
python -m scripts.seed_role_permissions
python -m scripts.check_role_permissions
```

Seed scripts should be designed to be safe to run more than once where possible.

---

## 13. Backend Enforcement

Permissions must always be enforced by the backend.

The frontend may hide unavailable buttons for usability, but this is not considered security.

Incorrect:

```text
Frontend hides "Delete"
→ therefore user cannot delete
```

Correct:

```text
Frontend
   │
   ▼
API Request
   │
   ▼
JWT Authentication
   │
   ▼
Company Permission Check
   │
   ▼
Business Operation
```

The API remains the authoritative security boundary.

---

## 14. Company Isolation

A permission in one company must not grant access to another company.

For example:

```text
User 10
│
├── Company 1 → accountant
│
└── Company 2 → seller
```

A request for:

```text
Company 1 / accounts
```

is evaluated using the role for Company 1.

The seller role from Company 2 must not affect that decision.

---

## 15. Future RBAC Development

As additional ERP modules are introduced, new permissions will be added.

Expected permission groups include:

```text
journal.*
ledger.*
sales.*
purchases.*
inventory.*
bank.*
cash.*
vat.*
tax.*
reports.*
settings.*
```

Sensitive actions should use more specific permissions.

For example, creating a journal entry and posting it should eventually be separate capabilities:

```text
journal.create
journal.post
```

This allows workflows where one employee prepares a transaction and another employee approves or posts it.

---

## 16. Security Principles

The RBAC architecture follows these rules:

1. Authentication does not automatically imply authorization.
2. Company resources require company-scoped permission checks.
3. Users may have different roles in different companies.
4. Inactive company roles must not grant permissions.
5. Permission checks must be performed on the backend.
6. The client application must never be treated as a security boundary.
7. Sensitive actions should have dedicated permissions.
8. Role changes should eventually be auditable.
9. Permission changes should eventually be auditable.
10. Financial approval and posting permissions should be separable.