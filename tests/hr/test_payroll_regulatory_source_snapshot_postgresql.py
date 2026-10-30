"""Real PostgreSQL persistence and rollback of regulatory evidence.

Never treats SOURCE_EVIDENCE_V1 as an official statutory form.
All writes occur on the isolated PostgreSQL database copy.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import seed

from app.models.employee import Employee
from app.models.payroll import PayrollPeriod
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services.payroll_regulatory_report_service import (
    create_payroll_regulatory_report,
    PayrollRegulatoryReportError,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    persist_regulatory_source_snapshot,
    SOURCE_CODE,
)

pytestmark = pytest.mark.asyncio


async def prepare_case(db):
    company, user, contract, calc, bank, beneficiary, claim = (
        await seed(db)
    )

    employee = await db.get(Employee, contract.employee_id)
    period = await db.get(PayrollPeriod, calc.payroll_period_id)

    assert employee is not None
    assert period is not None

    if not employee.tax_number:
        employee.tax_number = (
            f"REGULATORY-E2E-{employee.id:010d}"
        )

    if getattr(period.status, "value", period.status) == "draft":
        period.status = "finalized"
        period.finalized_by = user.id
        period.finalized_at = datetime.now(timezone.utc)

    await db.flush()

    report = await create_payroll_regulatory_report(
        db,
        company_id=company.id,
        payroll_period_id=period.id,
        report_kind="4df",
        created_by=user.id,
    )

    assert report.status == "draft"
    return company, user, calc, report


async def test_real_snapshot_persistence_and_idempotency(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calc, report = await prepare_case(db)

        rows = await persist_regulatory_source_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=report.id,
        )

        assert rows
        assert all(row.id is not None for row in rows)
        assert all(row.row_code == SOURCE_CODE for row in rows)
        assert any(
            row.payroll_calculation_id == calc.id
            for row in rows
        )

        await db.flush()

        company_id = company.id
        report_id = report.id
        expected_row_ids = [row.id for row in rows]

        assert all(row_id is not None for row_id in expected_row_ids)

        db.expire_all()

        persisted = (
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                        == company_id,
                    PayrollRegulatoryReportRow
                        .payroll_regulatory_report_id == report_id,
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all()

        assert len(persisted) == len(rows)

        repeated = await persist_regulatory_source_snapshot(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
        )

        assert [row.id for row in repeated] == expected_row_ids
        assert [row.id for row in persisted] == expected_row_ids

        stored_report = await db.get(
            PayrollRegulatoryReport, report_id
        )

        assert stored_report.status == "draft"
        assert stored_report.generated_at is None
        assert stored_report.generated_by is None

        await db.rollback()


async def test_cross_company_access_refused(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calc, report = await prepare_case(db)

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="not found",
        ):
            await persist_regulatory_source_snapshot(
                db,
                company_id=company.id + 1000000,
                payroll_regulatory_report_id=report.id,
            )

        await db.rollback()


async def test_rollback_does_not_leave_snapshot_rows(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, calc, report = await prepare_case(db)

        await persist_regulatory_source_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=report.id,
        )

        report_id = report.id
        company_id = company.id

        await db.rollback()

    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id
                    == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report_id,
            )
        )
        assert count == 0
