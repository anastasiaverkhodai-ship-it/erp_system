import csv
import io

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_dated_4df_persistence_postgresql import (
    prepare,
)

import app.services.payroll_regulatory_4df_review_export as export_service
import app.services.payroll_regulatory_dated_4df_service as writer_service
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


pytestmark = pytest.mark.asyncio


async def test_real_postgresql_dated_4df_review_export(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id, allocations = await prepare(
            db, monkeypatch
        )

        # Readiness is test-scoped; PostgreSQL report rows are real.
        import app.services.payroll_regulatory_readiness_service as readiness_module

        monkeypatch.setattr(
            readiness_module,
            "payroll_regulatory_readiness",
            writer_service.payroll_regulatory_readiness,
        )

        await writer_service.persist_dated_4df_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            reviewed_allocations=allocations,
        )

        first = await export_service.export_4df_financial_review(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
        )

        second = await export_service.export_4df_financial_review(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
        )

        assert first == second
        assert first.row_count == 2
        assert first.calculation_count == 1
        assert len(first.sha256) == 64

        records = list(
            csv.DictReader(io.StringIO(first.csv_text))
        )

        assert len(records) == 2
        assert all(
            item["document_type"]
            == "INTERNAL_REVIEW_NOT_FOR_FILING"
            for item in records
        )
        assert records[0]["payment_date"] == "2026-10-20"
        assert records[1]["payment_date"] == "2026-10-25"
        assert records[0]["gross_accrued_amount"] == "1000.00"
        assert records[1]["gross_accrued_amount"] == "0.00"

        await db.rollback()


async def test_cross_company_review_export_is_rejected(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id, allocations = await prepare(
            db, monkeypatch
        )

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="not found",
        ):
            await export_service.export_4df_financial_review(
                db,
                company_id=company_id + 1000000,
                payroll_regulatory_report_id=report_id,
            )

        await db.rollback()
