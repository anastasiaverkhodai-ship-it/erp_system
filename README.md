# ERP System

ERP System is a backend platform for a future full-scale enterprise resource planning and accounting system.

The project is designed for Ukrainian businesses and is intended to support:

- Accounting
- Tax accounting
- Management accounting
- Inventory and warehouse management
- Sales
- Purchases
- Banking
- Cash operations
- Financial reporting
- Tax reporting
- Role-based access control
- Multi-company support

The architecture is inspired by enterprise accounting systems such as 1C UTP, while being implemented as an independent modern ERP platform.

---

## Technology Stack

### Backend

- Python 3.12
- FastAPI
- SQLAlchemy 2.0 Async
- Pydantic v2
- Alembic
- Uvicorn

### Database

- PostgreSQL 16
- asyncpg

### Infrastructure

- Docker
- Docker Compose
- Redis

### Security

- JWT authentication
- Access tokens
- Refresh tokens
- Argon2 password hashing
- Role-Based Access Control (RBAC)
- Company-based permissions

---

## Current Project Status

Version: `0.1`

Implemented:

- FastAPI application
- PostgreSQL database
- Docker environment
- Async SQLAlchemy
- Alembic migrations
- User authentication
- JWT access and refresh tokens
- Argon2 password hashing
- User model
- Roles
- Permissions
- Global RBAC
- Company-based RBAC
- Companies
- Company users
- Role assignment inside companies
- User access deactivation
- Accounting periods
- Accounting period locking
- Chart of Accounts
- Hierarchical accounting accounts

---

## Project Structure

```text
app/
├── api/
│   └── v1/
├── core/
├── dependencies/
├── models/
├── repositories/
├── schemas/
├── services/
├── worker/
└── main.py