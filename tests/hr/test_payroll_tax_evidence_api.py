"""Payroll tax evidence API security and transaction tests."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.dialects import postgresql

from app.main import app
from app.api.v1.payroll_tax_evidence import (
    post_tax_evidence,
    list_tax_evidence,
    read_tax_evidence,
    post_verify_tax_evidence,
)
from app.schemas.payroll_tax_evidence import (
    TaxEvidenceCreate,
    TaxEvidenceVerify,
)
from app.services.payroll_tax_evidence_service import (
    PayrollTaxEvidenceError,
)


PREFIX = "/api/v1/companies/{company_id}/payroll-tax-evidence"


def make_db():
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.scalars = AsyncMock()
    return db


def make_data():
    return TaxEvidenceCreate(
        employee_id=20,
        document_type="certificate",
        document_number="TEST-001",
        issued_on=date(2026, 1, 1),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        entitlement_type="benefit",
        entitlement_code="TEST_BENEFIT",
    )


def make_row():
    return SimpleNamespace(
        id=10,
        company_id=1,
        employee_id=20,
        verification_status="pending",
    )


def test_openapi_operations():
    paths = app.openapi()["paths"]

    assert set(paths[PREFIX]) == {"get", "post"}
    assert set(paths[PREFIX + "/{evidence_id}"]) == {"get"}
    assert set(
        paths[PREFIX + "/{evidence_id}/verify"]
    ) == {"post"}


def test_all_routes_have_permission_dependencies():
    expected = {
        ("POST", PREFIX): "employees.manage",
        ("GET", PREFIX): "employees.read",
        ("GET", PREFIX + "/{evidence_id}"): "employees.read",
        (
            "POST",
            PREFIX + "/{evidence_id}/verify",
        ): "employees.manage",
    }

    # Validate the exact route registrations exposed by OpenAPI.
    schema = app.openapi()

    for (method, path), permission in expected.items():
        assert path in schema["paths"], path
        assert method.lower() in schema["paths"][path], (
            method,
            path,
        )

    # Inspect the router definitions directly.
    from app.api.v1.payroll_tax_evidence import router

    routes = {
        (method.upper(), route.path): route
        for route in router.routes
        for method in getattr(route, "methods", ())
    }

    for (method, full_path), permission in expected.items():
        local_path = full_path.removeprefix("/api/v1")

        route = routes.get((method, local_path))

        assert route is not None, (
            method,
            local_path,
            sorted(routes),
        )

        dependencies = [
            dependency.call
            for dependency in route.dependant.dependencies
        ]

        assert dependencies, (
            "Endpoint has no dependencies",
            method,
            local_path,
        )

        def dependency_has_permission(dependency):
            closure = (
                getattr(dependency, "__closure__", None)
                or ()
            )

            return any(
                cell.cell_contents == permission
                for cell in closure
            )

        assert any(
            dependency_has_permission(dependency)
            for dependency in dependencies
        ), (
            "Required company permission not found",
            permission,
            method,
            local_path,
        )


@pytest.mark.asyncio
async def test_create_commits_and_refreshes():
    db = make_db()
    row = make_row()
    actor = SimpleNamespace(id=5)

    with patch(
        "app.api.v1.payroll_tax_evidence.create_tax_evidence",
        new_callable=AsyncMock,
        return_value=row,
    ) as service:
        result = await post_tax_evidence(
            company_id=1,
            data=make_data(),
            db=db,
            actor=actor,
        )

    assert result is row

    assert service.await_args.kwargs["company_id"] == 1
    assert service.await_args.kwargs["created_by"] == 5

    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(row)
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rolls_back_on_validation_error():
    db = make_db()

    with patch(
        "app.api.v1.payroll_tax_evidence.create_tax_evidence",
        new_callable=AsyncMock,
        side_effect=PayrollTaxEvidenceError(
            "Employee not found in this company"
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await post_tax_evidence(
                company_id=1,
                data=make_data(),
                db=db,
                actor=SimpleNamespace(id=5),
            )

    assert error.value.status_code == 404
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_commits():
    db = make_db()
    row = make_row()

    with patch(
        "app.api.v1.payroll_tax_evidence.verify_tax_evidence",
        new_callable=AsyncMock,
        return_value=row,
    ) as service:
        result = await post_verify_tax_evidence(
            company_id=1,
            evidence_id=10,
            data=TaxEvidenceVerify(approved=True),
            db=db,
            actor=SimpleNamespace(id=5),
        )

    assert result is row

    assert service.await_args.kwargs == {
        "company_id": 1,
        "evidence_id": 10,
        "verified_by": 5,
        "approved": True,
    }

    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_repeated_verification_returns_conflict():
    db = make_db()

    with patch(
        "app.api.v1.payroll_tax_evidence.verify_tax_evidence",
        new_callable=AsyncMock,
        side_effect=PayrollTaxEvidenceError(
            "Evidence has already been reviewed"
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await post_verify_tax_evidence(
                company_id=1,
                evidence_id=10,
                data=TaxEvidenceVerify(approved=True),
                db=db,
                actor=SimpleNamespace(id=5),
            )

    assert error.value.status_code == 409

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_passes_approved_false():
    db = make_db()

    with patch(
        "app.api.v1.payroll_tax_evidence.verify_tax_evidence",
        new_callable=AsyncMock,
        return_value=make_row(),
    ) as service:
        await post_verify_tax_evidence(
            company_id=1,
            evidence_id=10,
            data=TaxEvidenceVerify(approved=False),
            db=db,
            actor=SimpleNamespace(id=5),
        )

    assert service.await_args.kwargs["approved"] is False


@pytest.mark.asyncio
async def test_read_is_company_scoped():
    db = make_db()

    with patch(
        "app.api.v1.payroll_tax_evidence.get_tax_evidence",
        new_callable=AsyncMock,
        return_value=make_row(),
    ) as service:
        await read_tax_evidence(
            company_id=1,
            evidence_id=10,
            db=db,
            actor=SimpleNamespace(id=5),
        )

    assert service.await_args.kwargs == {
        "company_id": 1,
        "evidence_id": 10,
    }


@pytest.mark.asyncio
async def test_list_query_filters_company_and_employee():
    db = make_db()

    result = MagicMock()
    result.all.return_value = []
    db.scalars.return_value = result

    await list_tax_evidence(
        company_id=1,
        employee_id=20,
        db=db,
        actor=SimpleNamespace(id=5),
    )

    query = db.scalars.await_args.args[0]

    compiled = query.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )

    sql = str(compiled)

    assert "company_id = 1" in sql
    assert "employee_id = 20" in sql


def test_invalid_create_dates_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaxEvidenceCreate(
            employee_id=20,
            document_type="certificate",
            document_number="TEST-001",
            issued_on=date(2026, 2, 1),
            valid_from=date(2026, 1, 1),
            entitlement_type="benefit",
            entitlement_code="TEST_BENEFIT",
        )
