# Development Guide

## 1. Overview

This document describes how to configure, run and maintain the ERP System development environment.

The current backend stack includes:

- Python 3.12
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0 Async
- asyncpg
- Alembic
- Pydantic
- Docker / Docker Compose
- JWT authentication
- Argon2 password hashing

---

## 2. Project Directory

The project root is:

```text
erp_system/
```

All development commands should normally be executed from this directory.

Example:

```bash
cd ~/Projects/erp_system
```

---

## 3. Python Virtual Environment

Create a virtual environment:

```bash
python3.12 -m venv .venv
```

Activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

The terminal should then show:

```text
(.venv)
```

To deactivate:

```bash
deactivate
```

---

## 4. Install Dependencies

Install project dependencies using the project's dependency file when available.

For example:

```bash
pip install -r requirements.txt
```

Important backend packages include:

```text
fastapi
uvicorn
sqlalchemy
asyncpg
alembic
pydantic
pyjwt
pwdlib
argon2-cffi
```

Password hashing uses:

```text
pwdlib + Argon2
```

The project does not rely on Passlib/Bcrypt for the current password-hashing implementation.

---

## 5. Environment Configuration

Sensitive configuration must be stored in environment variables.

The local development environment uses:

```text
.env
```

Typical settings include:

```text
Database connection
JWT secret
JWT algorithm
Access token expiration
Refresh token expiration
```

Secrets must never be committed to Git.

A future public repository configuration should provide:

```text
.env.example
```

containing example variable names without real secrets.

---

## 6. PostgreSQL

PostgreSQL is the primary ERP database.

The development database uses a dedicated database and database user.

Database schema creation and updates must be performed through Alembic rather than manually creating application tables.

---

## 7. Docker

Start configured Docker services:

```bash
docker compose up -d
```

Check running containers:

```bash
docker compose ps
```

Stop services:

```bash
docker compose down
```

To inspect logs:

```bash
docker compose logs
```

---

## 8. Alembic Migrations

Check the currently applied migration:

```bash
alembic current
```

Check the latest migration:

```bash
alembic heads
```

View migration history:

```bash
alembic history
```

Apply all pending migrations:

```bash
alembic upgrade head
```

---

## 9. Creating a Migration

After changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "migration description"
```

Always review the generated migration before running:

```bash
alembic upgrade head
```

A generated migration must not be assumed to be correct automatically.

Check especially:

```text
create_table
drop_table
foreign keys
unique constraints
indexes
nullable changes
unexpected schema changes
upgrade()
downgrade()
```

---

## 10. Empty Alembic Migrations

If Alembic generates:

```python
def upgrade():
    pass
```

when a schema change was expected, do not apply the migration immediately.

First verify that the model is imported into SQLAlchemy metadata.

For example:

```python
from app.models.account import Account
```

Then verify metadata:

```bash
python -c "from app.core.database import Base; import app.models; print(sorted(Base.metadata.tables.keys()))"
```

The expected table should appear in the output.

---

## 11. Running the API

Start the development server:

```bash
uvicorn app.main:app --reload
```

The API runs locally on port `8000` by default.

The root endpoint can be used to verify that the application is running.

The health endpoint is:

```text
/health
```

---

## 12. Swagger UI

FastAPI automatically provides interactive API documentation.

Swagger UI is available at:

```text
/docs
```

Swagger can be used to:

- inspect endpoints
- test requests
- authenticate
- test company permissions
- inspect request schemas
- inspect response schemas

---

## 13. Authentication Testing

Login using the authentication endpoint.

After receiving an access token, protected API requests use:

```text
Authorization: Bearer <access_token>
```

Swagger's Authorize function can also manage the bearer token automatically.

---

## 14. RBAC Seed Data

Roles and permissions are initialized using development scripts.

Main scripts include:

```text
scripts/seed_rbac.py
scripts/seed_role_permissions.py
```

Run:

```bash
python -m scripts.seed_rbac
```

Then:

```bash
python -m scripts.seed_role_permissions
```

---

## 15. RBAC Verification

Verify base roles and permissions:

```bash
python -m scripts.check_rbac
```

Verify role-permission assignments:

```bash
python -m scripts.check_role_permissions
```

This is useful after changing permission definitions.

---

## 16. Importing Project Modules

Development scripts should normally be executed as Python modules from the project root.

Preferred:

```bash
python -m scripts.seed_rbac
```

Instead of:

```bash
python scripts/seed_rbac.py
```

Running scripts as modules ensures that the project root is available for imports such as:

```python
from app.core.database import ...
```

---

## 17. Basic Import Checks

Before starting the server, individual modules can be tested.

Example:

```bash
python -c "from app.main import app; print('App OK')"
```

Accounting periods:

```bash
python -c "from app.api.v1.accounting_periods import router; print('Accounting Periods API OK')"
```

Chart of Accounts:

```bash
python -c "from app.api.v1.accounts import router; print('Chart of Accounts API OK')"
```

---

## 18. Database Metadata Check

To inspect all SQLAlchemy tables currently registered:

```bash
python -c "from app.core.database import Base; import app.models; print(sorted(Base.metadata.tables.keys()))"
```

This is particularly useful before generating Alembic migrations.

---

## 19. Recommended Development Workflow

Use the following workflow when implementing a new database-backed feature:

```text
1. Design feature
        ↓
2. Create/update SQLAlchemy model
        ↓
3. Import model into metadata
        ↓
4. Verify Base.metadata
        ↓
5. Generate Alembic migration
        ↓
6. Review migration manually
        ↓
7. Apply migration
        ↓
8. Add Pydantic schemas
        ↓
9. Add service/business logic
        ↓
10. Add API endpoints
        ↓
11. Add permissions
        ↓
12. Test in Swagger
        ↓
13. Update documentation
```

---

## 20. Accounting Development Rule

Any operation that creates financial movements must eventually verify the accounting period.

Reusable logic exists in:

```text
app/services/accounting_period_service.py
```

using:

```text
ensure_period_open()
```

Future accounting, banking and document-posting services should reuse this rule rather than implementing independent period checks.

---

## 21. Git Workflow

Before committing:

```bash
git status
```

Review changes:

```bash
git diff
```

Add changes:

```bash
git add .
```

Create a descriptive commit:

```bash
git commit -m "Add accounting core documentation"
```

Then push to the configured remote branch when appropriate.

Never commit:

```text
.env
passwords
JWT secrets
database credentials
private keys
```

---

## 22. Troubleshooting

### Target database is not up to date

If Alembic reports:

```text
Target database is not up to date
```

compare:

```bash
alembic current
alembic heads
```

Do not randomly delete or apply migration files before determining why the database and migration history differ.

### ModuleNotFoundError: app

Run scripts from the project root using:

```bash
python -m scripts.<script_name>
```

### Import errors after creating a module

Check:

- file name
- import path
- package structure
- model registration
- virtual environment

---

## 23. Development Principles

1. Work from the project root.
2. Keep the virtual environment activated.
3. Use Alembic for database schema changes.
4. Review generated migrations before applying them.
5. Keep secrets outside source control.
6. Run scripts as modules when they import the application.
7. Keep API routes relatively thin.
8. Put reusable business logic into services.
9. Enforce permissions on the backend.
10. Update documentation when architecture or business rules change.