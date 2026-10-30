
"""Contract and handler tests for company-scoped 4DF review CSV."""
import hashlib
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import app.api.v1.payroll_regulatory_reports as api
import app.services.payroll_regulatory_4df_review_export as service
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def test_export_route_and_company_permission():
    routes = [
        route for route in api.router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith(
            "/payroll-regulatory-reports/{report_id}/4df-review.csv"
        )
    ]
    assert len(routes) == 1
    route = routes[0]
    assert route.methods == {"GET"}

    # Match the dependency contract of the existing read endpoint.
    read_route = next(
        route for route in api.router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith(
            "/payroll-regulatory-reports/{report_id}/rows"
        )
    )

    export_dependencies = [
        dependency.call
        for dependency in route.dependant.dependencies
    ]
    read_dependencies = [
        dependency.call
        for dependency in read_route.dependant.dependencies
    ]

    assert len(export_dependencies) == len(read_dependencies)
    assert len(export_dependencies) >= 2

    # get_db is a shared dependency. The permission checker is
    # a freshly created closure, so function identity differs.
    assert export_dependencies[0] is read_dependencies[0]

    def captured_permission(checker):
        assert checker.__name__ == "permission_checker"
        assert checker.__closure__ is not None

        captured = [
            cell.cell_contents
            for cell in checker.__closure__
        ]

        assert "employees.read" in captured
        return "employees.read"

    assert (
        captured_permission(export_dependencies[1])
        == captured_permission(read_dependencies[1])
    )

    # They must use the same checker implementation even though
    # each Depends declaration creates a distinct closure.
    assert (
        export_dependencies[1].__code__
        is read_dependencies[1].__code__
    )


@pytest.mark.asyncio
async def test_export_returns_csv_with_no_cache(monkeypatch):
    content = (
        "document_type,payment_date\\n"
        "INTERNAL_REVIEW_NOT_FOR_FILING,2026-10-20\\n"
    ).replace("\\n", "\n")

    digest = hashlib.sha256(content.encode()).hexdigest()

    async def stub(db, *, company_id, payroll_regulatory_report_id):
        assert company_id == 7
        assert payroll_regulatory_report_id == 12
        return SimpleNamespace(
            csv_text=content,
            sha256=digest,
            row_count=1,
            calculation_count=1,
        )

    monkeypatch.setattr(
        service, "export_4df_financial_review", stub
    )

    response = await api.download_4df_financial_review(
        company_id=7,
        report_id=12,
        db=object(),
        _=object(),
    )

    assert response.status_code == 200
    assert response.body.decode() == content
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-sha256"] == digest
    assert response.headers["x-4df-row-count"] == "1"
    assert response.headers["x-4df-document-type"] == (
        "INTERNAL_REVIEW_NOT_FOR_FILING"
    )
    assert "attachment;" in response.headers[
        "content-disposition"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,expected",
    [
        ("Regulatory report not found", 404),
        ("Only draft 4DF evidence can be exported for review", 409),
        ("Review export does not cover current payroll sources", 409),
    ],
)
async def test_export_fails_closed(
    monkeypatch, reason, expected
):
    async def stub(db, **kwargs):
        raise PayrollRegulatoryReportError(reason)

    monkeypatch.setattr(
        service, "export_4df_financial_review", stub
    )

    with pytest.raises(HTTPException) as exc:
        await api.download_4df_financial_review(
            company_id=7,
            report_id=12,
            db=object(),
            _=object(),
        )

    assert exc.value.status_code == expected
