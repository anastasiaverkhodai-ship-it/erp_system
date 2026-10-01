import ast
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models
from app.core.config import settings
from app.core.database import Base
from app.main import app
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.user import User
from app.models.user_company import user_companies
from app.services.employee_service import (
    EmployeeDuplicateError,
    EmployeeLifecycleError,
    EmployeeNotFoundError,
    EmployeeUserLinkError,
    create_employee,
    get_employee,
    list_employees,
    update_employee,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Set RUN_POSTGRES_E2E=1",
    ),
]


def minimal_kwargs(model, explicit):
    values = dict(explicit)

    for column in model.__table__.columns:
        if column.name in values:
            continue
        if column.primary_key:
            continue
        if column.nullable:
            continue
        if column.default is not None:
            continue
        if column.server_default is not None:
            continue
        if column.foreign_keys:
            continue

        try:
            python_type = column.type.python_type
        except Exception:
            python_type = None

        if python_type is str:
            values[column.name] = (
                f"test-{column.name}-{uuid4().hex[:8]}"
            )
        elif python_type is bool:
            values[column.name] = True
        elif python_type is int:
            values[column.name] = 1
        elif python_type is date:
            values[column.name] = date(2026, 1, 1)
        else:
            raise RuntimeError(
                f"Cannot infer required field "
                f"{model.__name__}.{column.name}: "
                f"{column.type}"
            )

    return values


@pytest_asyncio.fixture
async def employee_engine():
    admin = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )

    schema = "test_employee_" + uuid4().hex

    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "search_path": schema,
            }
        },
    )

    try:
        async with admin.begin() as conn:
            await conn.execute(
                text(f'CREATE SCHEMA "{schema}"')
            )

        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all
            )

        yield engine

    finally:
        await engine.dispose()

        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f'DROP SCHEMA IF EXISTS '
                    f'"{schema}" CASCADE'
                )
            )

        await admin.dispose()


async def seed(db):
    suffix = uuid4().hex[:10]

    company = Company(
        **minimal_kwargs(
            Company,
            {
                "name": f"Employee company {suffix}",
            },
        )
    )

    other_company = Company(
        **minimal_kwargs(
            Company,
            {
                "name": f"Employee other {suffix}",
            },
        )
    )

    db.add_all(
        [
            company,
            other_company,
        ]
    )
    await db.flush()

    actor = User(
        **minimal_kwargs(
            User,
            {
                "email": (
                    f"employee-actor-{suffix}"
                    "@example.test"
                ),
                "password_hash": "unused",
                "first_name": "Employee",
                "last_name": "Actor",
            },
        )
    )

    linked_user = User(
        **minimal_kwargs(
            User,
            {
                "email": (
                    f"employee-linked-{suffix}"
                    "@example.test"
                ),
                "password_hash": "unused",
                "first_name": "Linked",
                "last_name": "User",
            },
        )
    )

    foreign_user = User(
        **minimal_kwargs(
            User,
            {
                "email": (
                    f"employee-foreign-{suffix}"
                    "@example.test"
                ),
                "password_hash": "unused",
                "first_name": "Foreign",
                "last_name": "User",
            },
        )
    )

    db.add_all(
        [
            actor,
            linked_user,
            foreign_user,
        ]
    )
    await db.flush()

    await db.execute(
        insert(user_companies),
        [
            {
                "user_id": actor.id,
                "company_id": company.id,
            },
            {
                "user_id": linked_user.id,
                "company_id": company.id,
            },
            {
                "user_id": foreign_user.id,
                "company_id": other_company.id,
            },
        ],
    )

    await db.commit()

    return {
        "company": company.id,
        "other_company": other_company.id,
        "actor": actor.id,
        "linked_user": linked_user.id,
        "foreign_user": foreign_user.id,
    }


async def create(
    db,
    f,
    *,
    company=None,
    number="EMP-001",
    user_id=None,
    hire_date=date(2026, 1, 10),
    termination_date=None,
    status=EmployeeStatus.ACTIVE,
):
    return await create_employee(
        db,
        company_id=(
            company
            if company is not None
            else f["company"]
        ),
        employee_number=number,
        first_name="Anna",
        last_name="Employee",
        middle_name=None,
        tax_number=None,
        birth_date=None,
        hire_date=hire_date,
        termination_date=termination_date,
        status=status,
        user_id=user_id,
        created_by=f["actor"],
    )


def test_employee_model_metadata():
    table = Employee.__table__

    assert table.name == "employees"

    expected = {
        "id",
        "company_id",
        "employee_number",
        "first_name",
        "last_name",
        "middle_name",
        "tax_number",
        "birth_date",
        "payment_iban",
        "hire_date",
        "termination_date",
        "status",
        "user_id",
        "created_by",
        "created_at",
        "updated_at",
    }

    assert set(table.columns.keys()) == expected

    names = {
        item.name
        for item in table.constraints
        if item.name
    }

    assert (
        "uq_employees_company_employee_number"
        in names
    )
    assert (
        "ck_employees_termination_not_before_hire"
        in names
    )
    assert (
        "ck_employees_terminated_requires_date"
        in names
    )


async def test_company_scoped_employee_number_uniqueness(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        await create(
            db,
            f,
            number="EMP-UNIQUE",
        )

        with pytest.raises(
            EmployeeDuplicateError
        ):
            await create(
                db,
                f,
                number="EMP-UNIQUE",
            )


async def test_same_employee_number_across_companies(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        first = await create(
            db,
            f,
            number="EMP-SAME",
        )

        second = await create(
            db,
            f,
            company=f["other_company"],
            number="EMP-SAME",
        )

        assert first.company_id != second.company_id
        assert (
            first.employee_number
            == second.employee_number
        )


async def test_create_employee(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        employee = await create(
            db,
            f,
            number=" EMP-100 ",
        )

        assert employee.id is not None
        assert employee.company_id == f["company"]
        assert employee.employee_number == "EMP-100"
        assert employee.first_name == "Anna"
        assert employee.status == EmployeeStatus.ACTIVE
        assert employee.created_by == f["actor"]


async def test_list_employees_company_isolated(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        local = await create(
            db,
            f,
            number="EMP-LOCAL",
        )

        await create(
            db,
            f,
            company=f["other_company"],
            number="EMP-FOREIGN",
        )

        rows = await list_employees(
            db,
            company_id=f["company"],
        )

        assert [row.id for row in rows] == [local.id]


async def test_get_employee_company_isolated(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        employee = await create(
            db,
            f,
            number="EMP-GET",
        )

        loaded = await get_employee(
            db,
            company_id=f["company"],
            employee_id=employee.id,
        )

        assert loaded.id == employee.id

        with pytest.raises(
            EmployeeNotFoundError
        ):
            await get_employee(
                db,
                company_id=f["other_company"],
                employee_id=employee.id,
            )


async def test_update_employee(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        employee = await create(
            db,
            f,
            number="EMP-UPDATE",
        )

        updated = await update_employee(
            db,
            company_id=f["company"],
            employee_id=employee.id,
            first_name="Updated",
            tax_number=" 1234567890 ",
            fields_set={
                "first_name",
                "tax_number",
            },
        )

        assert updated.first_name == "Updated"
        assert updated.tax_number == "1234567890"


async def test_optional_valid_user_link(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        without_link = await create(
            db,
            f,
            number="EMP-NOLINK",
        )

        with_link = await create(
            db,
            f,
            number="EMP-LINK",
            user_id=f["linked_user"],
        )

        assert without_link.user_id is None
        assert with_link.user_id == f["linked_user"]


async def test_cross_company_user_link_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            EmployeeUserLinkError
        ):
            await create(
                db,
                f,
                number="EMP-BADLINK",
                user_id=f["foreign_user"],
            )


async def test_termination_date_chronology(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            EmployeeLifecycleError,
            match="cannot precede",
        ):
            await create(
                db,
                f,
                number="EMP-DATE",
                hire_date=date(2026, 2, 1),
                termination_date=date(2026, 1, 31),
                status=EmployeeStatus.TERMINATED,
            )


async def test_terminated_requires_termination_date(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        f = await seed(db)

        with pytest.raises(
            EmployeeLifecycleError,
            match="requires termination_date",
        ):
            await create(
                db,
                f,
                number="EMP-TERM",
                status=EmployeeStatus.TERMINATED,
            )


def test_service_flush_only():
    source = Path(
        "app/services/employee_service.py"
    ).read_text()

    tree = ast.parse(source)

    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]

    assert "flush" in calls
    assert "commit" not in calls
    assert "rollback" not in calls


def test_api_transaction_ownership():
    source = Path(
        "app/api/v1/employees.py"
    ).read_text()

    tree = ast.parse(source)

    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]

    assert "commit" in calls
    assert "rollback" in calls


def test_openapi_employee_routes():
    paths = app.openapi()["paths"]

    collection = (
        "/api/v1/companies/{company_id}/employees"
    )
    detail = (
        "/api/v1/companies/{company_id}/employees/"
        "{employee_id}"
    )

    assert collection in paths
    assert detail in paths

    assert {"get", "post"} <= set(
        paths[collection]
    )
    assert {"get", "patch"} <= set(
        paths[detail]
    )


async def test_real_postgresql_employee_schema(
    employee_engine,
):
    async with employee_engine.connect() as conn:
        def inspect_schema(sync_conn):
            inspector = inspect(sync_conn)

            columns = {
                row["name"]
                for row in inspector.get_columns(
                    "employees"
                )
            }

            uniques = {
                row["name"]
                for row in inspector.get_unique_constraints(
                    "employees"
                )
            }

            indexes = {
                row["name"]
                for row in inspector.get_indexes(
                    "employees"
                )
            }

            foreign_keys = {
                tuple(row["constrained_columns"]):
                row["referred_table"]
                for row in inspector.get_foreign_keys(
                    "employees"
                )
            }

            return (
                columns,
                uniques,
                indexes,
                foreign_keys,
            )

        (
            columns,
            uniques,
            indexes,
            foreign_keys,
        ) = await conn.run_sync(inspect_schema)

    assert {
        "id",
        "company_id",
        "employee_number",
        "first_name",
        "last_name",
        "middle_name",
        "tax_number",
        "birth_date",
        "payment_iban",
        "hire_date",
        "termination_date",
        "status",
        "user_id",
        "created_by",
        "created_at",
        "updated_at",
    } == columns

    assert (
        "uq_employees_company_employee_number"
        in uniques
    )

    assert "ix_employees_company_status" in indexes
    assert "ix_employees_company_user" in indexes

    assert foreign_keys[("company_id",)] == "companies"
    assert foreign_keys[("user_id",)] == "users"
    assert foreign_keys[("created_by",)] == "users"
