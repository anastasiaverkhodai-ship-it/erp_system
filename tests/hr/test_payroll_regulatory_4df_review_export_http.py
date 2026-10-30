"""HTTP security tests for internal 4DF financial review export.

Uses the application's actual FastAPI permission dependency.
The export service is mocked in HTTP tests; its real PostgreSQL
read/write behavior is covered by separate persistence E2E.
"""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.v1 import payroll_regulatory_reports
from app.core.database import get_db

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


URL = (
    "/companies/{company_id}/"
    "payroll-regulatory-reports/{report_id}/4df-review.csv"
)

CSV_CONTENT = (
    "document_type,payment_date,paid_amount\n"
    "INTERNAL_REVIEW_NOT_FOR_FILING,2026-10-20,300.00\n"
    "INTERNAL_REVIEW_NOT_FOR_FILING,2026-10-25,470.00\n"
)


def setup_http_app(*, permission_allowed=True):
    api = FastAPI()
    api.include_router(payroll_regulatory_reports.router)

    db = AsyncMock()

    # Use the same permission fixture structure as the
    # existing payroll regulatory readiness HTTP tests.
    db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: (
            1 if permission_allowed else None
        )
    )

    api.dependency_overrides[get_current_user] = (
        lambda: SimpleNamespace(id=1)
    )

    async def session():
        yield db

    api.dependency_overrides[get_db] = session

    return api, db


def result():
    return SimpleNamespace(
        csv_text=CSV_CONTENT,
        sha256=hashlib.sha256(
            CSV_CONTENT.encode("utf-8")
        ).hexdigest(),
        row_count=2,
        calculation_count=1,
    )


@pytest.mark.asyncio
async def test_authorized_http_csv_download():
    api, db = setup_http_app(permission_allowed=True)

    with patch(
        "app.services.payroll_regulatory_4df_review_export."
        "export_4df_financial_review",
        new_callable=AsyncMock,
        return_value=result(),
    ) as export:

        async with AsyncClient(
            transport=ASGITransport(app=api),
            base_url="http://test",
        ) as client:
            response = await client.get(
                URL.format(company_id=7, report_id=12)
            )

        assert response.status_code == 200, response.text

        export.assert_awaited_once_with(
            db,
            company_id=7,
            payroll_regulatory_report_id=12,
        )

        assert response.text == CSV_CONTENT
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-4df-document-type"] == (
            "INTERNAL_REVIEW_NOT_FOR_FILING"
        )
        assert response.headers["x-4df-row-count"] == "2"

        assert response.headers["x-content-sha256"] == (
            hashlib.sha256(
                response.content
            ).hexdigest()
        )

        assert (
            "attachment;"
            in response.headers["content-disposition"]
        )

        assert (
            "INTERNAL-REVIEW.csv"
            in response.headers["content-disposition"]
        )

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_company_permission_returns_403():
    api, db = setup_http_app(permission_allowed=False)

    with patch(
        "app.services.payroll_regulatory_4df_review_export."
        "export_4df_financial_review",
        new_callable=AsyncMock,
    ) as export:

        async with AsyncClient(
            transport=ASGITransport(app=api),
            base_url="http://test",
        ) as client:
            response = await client.get(
                URL.format(company_id=7, report_id=12)
            )

        assert response.status_code == 403, response.text

        # Access must be rejected before opening payroll data.
        export.assert_not_awaited()

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_company_report_returns_404():
    api, db = setup_http_app(permission_allowed=True)

    async def reject_other_company(
        db_session,
        *,
        company_id,
        payroll_regulatory_report_id,
    ):
        assert db_session is db
        assert company_id == 8
        assert payroll_regulatory_report_id == 12

        raise PayrollRegulatoryReportError(
            "Regulatory report not found"
        )

    with patch(
        "app.services.payroll_regulatory_4df_review_export."
        "export_4df_financial_review",
        new_callable=AsyncMock,
        side_effect=reject_other_company,
    ) as export:

        async with AsyncClient(
            transport=ASGITransport(app=api),
            base_url="http://test",
        ) as client:
            response = await client.get(
                URL.format(company_id=8, report_id=12)
            )

        assert response.status_code == 404, response.text
        export.assert_awaited_once()

        assert "INTERNAL_REVIEW_NOT_FOR_FILING" not in (
            response.text
        )

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_report_state_returns_409():
    api, db = setup_http_app(permission_allowed=True)

    with patch(
        "app.services.payroll_regulatory_4df_review_export."
        "export_4df_financial_review",
        new_callable=AsyncMock,
        side_effect=PayrollRegulatoryReportError(
            "Only draft 4DF evidence can be exported for review"
        ),
    ) as export:

        async with AsyncClient(
            transport=ASGITransport(app=api),
            base_url="http://test",
        ) as client:
            response = await client.get(
                URL.format(company_id=7, report_id=12)
            )

        assert response.status_code == 409, response.text
        export.assert_awaited_once()

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_route_parameters_rejected():
    api, db = setup_http_app(permission_allowed=True)

    with patch(
        "app.services.payroll_regulatory_4df_review_export."
        "export_4df_financial_review",
        new_callable=AsyncMock,
    ) as export:

        async with AsyncClient(
            transport=ASGITransport(app=api),
            base_url="http://test",
        ) as client:
            response = await client.get(
                URL.format(
                    company_id="not-an-integer",
                    report_id=12,
                )
            )

        assert response.status_code == 422
        export.assert_not_awaited()

    db.commit.assert_not_awaited()
