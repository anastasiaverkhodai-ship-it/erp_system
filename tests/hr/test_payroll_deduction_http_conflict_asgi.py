"""Payroll deduction HTTP 409 regression.

Tests actual API router endpoints through FastAPI TestClient.
Authentication and database dependencies are replaced with
isolated fakes; no real PostgreSQL writes are performed.
"""

import importlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    PayrollDeductionResultError,
)


MESSAGE = (
    "Payroll deduction result is not authorized "
    "for financial execution"
)


CASES = (
    (
        "GET",
        "app.api.v1.payroll",
        "read_payroll_register",
        "/companies/1/payroll-periods/1/register",
        "get_payroll_register",
        None,
        False,
    ),
    (
        "POST",
        "app.api.v1.payroll_calculations",
        "generate_company_payroll_payslip",
        "/companies/1/payroll-calculations/1/payslip/generate",
        "generate_payroll_payslip",
        None,
        True,
    ),
    (
        "POST",
        "app.api.v1.payroll_disbursements",
        "api_create_payroll_disbursement",
        "/companies/1/payroll-calculations/1/disbursement",
        "create_payroll_disbursement",
        {
            "bank_account_id": 1,
            "payment_date": "2026-10-13",
            "amount": "10.00",
            "request_key": "r1-http-conflict",
        },
        True,
    ),
    (
        "POST",
        "app.api.v1.payroll_deductions",
        "api_post_payroll_deduction_journal",
        "/companies/1/payroll-deduction-results/1/accounting-journal",
        "generate_and_post_payroll_deduction_journal",
        None,
        True,
    ),
)


def walk_dependencies(dependant):
    for child in dependant.dependencies:
        yield child
        yield from walk_dependencies(child)


def get_verified_route(router, method, endpoint_name, request_path):
    matches = []

    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue

        if method not in route.methods:
            continue

        if route.endpoint.__name__ != endpoint_name:
            continue

        resolved = re.sub(
            r"\{[^}]+\}",
            "1",
            route.path,
        )

        if resolved != request_path:
            continue

        matches.append(route)

    assert len(matches) == 1, (
        f"Expected exactly one registered API route for "
        f"{endpoint_name}; found {len(matches)}"
    )

    return matches[0]


@pytest.mark.parametrize(
    (
        "method,module_name,endpoint_name,request_path,"
        "service_name,payload,expects_rollback"
    ),
    CASES,
)
def test_deduction_conflict_returns_http_409(
    monkeypatch,
    method,
    module_name,
    endpoint_name,
    request_path,
    service_name,
    payload,
    expects_rollback,
):
    module = importlib.import_module(module_name)
    router = module.router

    # Verify the route is really declared in its API router.
    source_route = get_verified_route(
        router,
        method,
        endpoint_name,
        request_path,
    )

    # Create a fully isolated FastAPI application using
    # the actual production router.
    isolated_app = FastAPI()
    isolated_app.include_router(router)

    db = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )

    actor = SimpleNamespace(
        id=1,
        is_active=True,
        is_superuser=True,
    )

    # The router can contain nested authentication and
    # company-permission dependencies.
    for dependency in walk_dependencies(source_route.dependant):
        callable_dependency = dependency.call

        if callable_dependency is None:
            continue

        name = getattr(
            callable_dependency,
            "__name__",
            "",
        )

        if name == "get_db":
            isolated_app.dependency_overrides[
                callable_dependency
            ] = lambda: db
        else:
            isolated_app.dependency_overrides[
                callable_dependency
            ] = lambda: actor

    # Replace only the specific business service reached
    # by the controller. The real conflict exception is raised.
    service_mock = AsyncMock(
        side_effect=PayrollDeductionResultConflictError(
            MESSAGE
        )
    )

    assert service_name in source_route.endpoint.__globals__

    monkeypatch.setitem(
        source_route.endpoint.__globals__,
        service_name,
        service_mock,
    )

    with TestClient(
        isolated_app,
        raise_server_exceptions=False,
    ) as client:
        if method == "GET":
            response = client.get(request_path)
        else:
            response = client.post(
                request_path,
                json=payload,
            )

    assert response.status_code == 409, (
        f"{method} {request_path}: "
        f"expected HTTP 409; got "
        f"{response.status_code}: {response.text}"
    )

    assert response.json() == {
        "detail": MESSAGE,
    }

    service_mock.assert_awaited_once()

    if expects_rollback:
        db.rollback.assert_awaited_once()
    else:
        db.rollback.assert_not_awaited()

    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()

    print(
        f"HTTP 409 PASS: {method} {request_path}",
        flush=True,
    )


def test_all_four_declared_routes_are_present():
    covered = set()

    for (
        method,
        module_name,
        endpoint_name,
        request_path,
        _service_name,
        _payload,
        _rollback,
    ) in CASES:
        module = importlib.import_module(module_name)

        route = get_verified_route(
            module.router,
            method,
            endpoint_name,
            request_path,
        )

        assert route.endpoint.__name__ == endpoint_name

        covered.add((method, request_path))

    assert len(covered) == 4


def test_conflict_retains_business_error_inheritance():
    assert issubclass(
        PayrollDeductionResultConflictError,
        PayrollDeductionResultError,
    )
