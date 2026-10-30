"""Real PostgreSQL generator integration acceptance.

The statutory registry remains unapproved. These tests verify
that real persisted evidence is reconciled and that failed
generation never promotes a draft to generated.
"""

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_dated_4df_persistence_postgresql import (
    prepare,
)

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)

from app.services.payroll_regulatory_dated_4df_service import (
    persist_dated_4df_rows,
)

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
    generate_payroll_regulatory_report,
)

pytestmark = pytest.mark.asyncio


async def initialize(db, monkeypatch):
    company_id, report_id, allocations = await prepare(
        db, monkeypatch
    )

    rows = await persist_dated_4df_rows(
        db,
        company_id=company_id,
        payroll_regulatory_report_id=report_id,
        reviewed_allocations=allocations,
    )

    assert len(rows) == 2

    report = await db.get(
        PayrollRegulatoryReport,
        report_id,
    )

    assert report is not None
    assert report.status == "draft"
    assert report.generated_at is None
    assert report.generated_by is None

    return company_id, report_id


async def assert_not_generated(db, company_id, report_id):
    report = await db.scalar(
        select(PayrollRegulatoryReport).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id == report_id,
        )
    )

    assert report is not None

    await db.refresh(report)

    assert report.status == "draft"
    assert report.generated_at is None
    assert report.generated_by is None


async def test_complete_dated_evidence_reaches_normative_gate(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        # Readiness is a local fixture substitution.
        # The report, rows, queries and generator use
        # actual PostgreSQL ORM persistence.
        import app.services.payroll_regulatory_readiness_service as readiness_module
        import app.services.payroll_regulatory_dated_4df_service as dated_module

        monkeypatch.setattr(
            readiness_module,
            "payroll_regulatory_readiness",
            dated_module.payroll_regulatory_readiness,
        )

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="4DF financial evidence reconciled;",
        ):
            await generate_payroll_regulatory_report(
                db,
                company_id=company_id,
                payroll_regulatory_report_id=report_id,
                generated_by=1,
            )

        await assert_not_generated(
            db, company_id, report_id
        )

        stored = list((
            await db.scalars(
                select(PayrollRegulatoryReportRow).where(
                    PayrollRegulatoryReportRow.company_id
                    == company_id,
                    PayrollRegulatoryReportRow
                        .payroll_regulatory_report_id
                        == report_id,
                )
            )
        ).all())

        assert len(stored) == 2

        await db.rollback()


async def test_incomplete_dated_evidence_rejected_before_mapping(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        import app.services.payroll_regulatory_readiness_service as readiness_module
        import app.services.payroll_regulatory_dated_4df_service as dated_module

        monkeypatch.setattr(
            readiness_module,
            "payroll_regulatory_readiness",
            dated_module.payroll_regulatory_readiness,
        )

        rows = list((
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                        == company_id,
                    PayrollRegulatoryReportRow
                        .payroll_regulatory_report_id
                        == report_id,
                )
                .order_by(
                    PayrollRegulatoryReportRow.row_no
                )
            )
        ).all())

        assert len(rows) == 2

        await db.delete(rows[1])
        await db.flush()

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="Incomplete 4DF dated slices",
        ):
            await generate_payroll_regulatory_report(
                db,
                company_id=company_id,
                payroll_regulatory_report_id=report_id,
                generated_by=1,
            )

        await assert_not_generated(
            db, company_id, report_id
        )

        await db.rollback()


async def test_cross_company_generation_rejected(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="not found",
        ):
            await generate_payroll_regulatory_report(
                db,
                company_id=company_id + 1000000,
                payroll_regulatory_report_id=report_id,
                generated_by=1,
            )

        await assert_not_generated(
            db, company_id, report_id
        )

        await db.rollback()
