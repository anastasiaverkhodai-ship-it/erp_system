"""D6 live employment-source binding on isolated PostgreSQL."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollPeriod

from app.services.payroll_regulatory_4df_d6_evidence_gate import (
    EvidenceGateError,
)
from app.services.payroll_regulatory_d6_live_hr_binding import (
    bind_d6_candidates_to_live_hr,
    verify_binding,
)


pytestmark = pytest.mark.asyncio


async def context(db):
    company_id, period_id = await prepare(db)

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == period_id,
        )
    )
    assert period is not None

    contracts = list((
        await db.scalars(
            select(EmploymentContract).where(
                EmploymentContract.company_id == company_id,
                EmploymentContract.start_date <= period.end_date,
                EmploymentContract.status != "cancelled",
            )
        )
    ).all())

    eligible = [
        c for c in contracts
        if c.end_date is None
        or c.end_date >= period.start_date
    ]

    assert eligible, "Seed must include an overlapping HR contract"
    contract = eligible[0]

    start = max(period.start_date, contract.start_date)
    end = min(
        period.end_date,
        contract.end_date
        if contract.end_date is not None
        else period.end_date,
    )
    assert start <= end

    return company_id, period_id, contract, start, end


def candidate(contract, start, end):
    return {
        "employee_id": contract.employee_id,
        "contract_id": contract.id,
        "basis_code": "12345678",
        "period_start": start,
        "period_end": end,
        "basis_evidence_reference":
            "TEST_ONLY_NOT_STATUTORILY_APPROVED",
    }


async def test_real_pg_live_hr_d6_binding(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        cid, pid, contract, start, end = await context(db)

        result = await bind_d6_candidates_to_live_hr(
            db,
            company_id=cid,
            payroll_period_id=pid,
            candidates=[candidate(contract, start, end)],
        )

        assert verify_binding(result)
        row = result["body"]["candidates"][0]
        assert row["hr_binding"] == "VERIFIED"
        assert row["basis_code_mapping"] == "NOT_APPROVED"
        assert result["body"]["official_generation"] == "BLOCKED"

        await db.rollback()


async def test_real_pg_d6_outside_contract_rejected(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        cid, pid, contract, start, end = await context(db)

        invalid = candidate(
            contract,
            date(2000, 1, 1),
            end,
        )

        with pytest.raises(
            EvidenceGateError, match="outside"
        ):
            await bind_d6_candidates_to_live_hr(
                db,
                company_id=cid,
                payroll_period_id=pid,
                candidates=[invalid],
            )

        await db.rollback()
