"""Real PostgreSQL writer persistence for dated 4DF draft rows.

The mapping/readiness/payment dependencies are test-scoped and
deliberately isolated. This tests persistence, idempotency,
immutability of existing rows and rollback, not statutory approval.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_source_snapshot_postgresql import (
    prepare_case,
)
from test_payroll_regulatory_dated_4df import case

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)
import app.services.payroll_regulatory_dated_4df_service as service

pytestmark = pytest.mark.asyncio


async def prepare(db, monkeypatch):
    company, user, calculation, report = await prepare_case(db)

    reference_report, readiness, approvals, allocations = case()

    company_id = company.id
    calculation_id = calculation.id
    report_id = report.id
    period_id = report.payroll_period_id
    readiness = deepcopy(readiness)
    readiness["company_id"] = company_id
    readiness["payroll_period_id"] = period_id
    readiness["report_kind"] = "4df"

    original = readiness["calculation_sources"][0]
    original["payroll_calculation_id"] = calculation_id

    from app.models.employment_contract import EmploymentContract
    contract = await db.get(
        EmploymentContract,
        calculation.employment_contract_id,
    )

    assert contract is not None
    assert contract.company_id == company_id

    verified_employee_id = int(contract.employee_id)
    verified_contract_id = int(contract.id)

    original["employee_id"] = verified_employee_id
    original["employment_contract_id"] = verified_contract_id

    for item in allocations:
        item["payroll_calculation_id"] = calculation_id

    async def readiness_stub(
        db_session,
        *,
        company_id,
        payroll_period_id,
        report_kind,
    ):
        assert company_id == readiness["company_id"]
        assert payroll_period_id == readiness["payroll_period_id"]
        assert report_kind == "4df"
        return deepcopy(readiness)

    async def verified_payment_stub(
        db_session,
        *,
        company_id,
        reviewed_allocations,
    ):
        assert company_id == readiness["company_id"]
        assert len(reviewed_allocations) == 2
        assert all(
            a["_verified_employee_id"] == verified_employee_id
            for a in reviewed_allocations
        )

    monkeypatch.setattr(
        service,
        "payroll_regulatory_readiness",
        readiness_stub,
    )
    monkeypatch.setattr(
        service,
        "approved_mappings_for",
        lambda *args: approvals,
    )
    monkeypatch.setattr(
        service,
        "verify_4df_posted_payment_evidence",
        verified_payment_stub,
    )

    return company_id, report_id, allocations


async def stored_rows(db, company_id, report_id):
    return list((
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id
                    == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id
                    == report_id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())


async def test_two_dated_rows_persist_and_repeat_without_duplicates(
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

        rows = await service.persist_dated_4df_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            reviewed_allocations=allocations,
        )

        assert len(rows) == 2
        await db.flush()

        expected_ids = [row.id for row in rows]
        assert all(expected_ids)

        db.expire_all()

        persisted = await stored_rows(db, company_id, report_id)

        assert len(persisted) == 2
        assert [row.id for row in persisted] == expected_ids
        assert [row.row_no for row in persisted] == [1, 2]

        repeated = await service.persist_dated_4df_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            reviewed_allocations=allocations,
        )

        assert [row.id for row in repeated] == expected_ids

        report = await db.get(
            PayrollRegulatoryReport,
            report_id,
        )
        assert report.status == "draft"
        assert report.generated_at is None

        await db.rollback()


async def test_changed_allocation_cannot_rewrite_snapshot(
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

        await service.persist_dated_4df_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            reviewed_allocations=allocations,
        )

        before = await stored_rows(db, company_id, report_id)
        original_payloads = [row.payload_json for row in before]

        changed = deepcopy(allocations)
        changed[0]["paid_amount"] = changed[0]["paid_amount"] - 1

        with pytest.raises(
            PayrollRegulatoryReportError,
            match="snapshot differs",
        ):
            await service.persist_dated_4df_rows(
                db,
                company_id=company_id,
                payroll_regulatory_report_id=report_id,
                reviewed_allocations=changed,
            )

        after = await stored_rows(db, company_id, report_id)
        assert [row.payload_json for row in after] == original_payloads

        await db.rollback()


async def test_cross_company_report_rejected(
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
            await service.persist_dated_4df_rows(
                db,
                company_id=company_id + 1000000,
                payroll_regulatory_report_id=report_id,
                reviewed_allocations=allocations,
            )

        await db.rollback()


async def test_rollback_removes_uncommitted_snapshot_rows(
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

        rows = await service.persist_dated_4df_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            reviewed_allocations=allocations,
        )
        assert len(rows) == 2

        await db.rollback()

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow
                    .payroll_regulatory_report_id == report_id,
            )
        )
        assert count == 0
