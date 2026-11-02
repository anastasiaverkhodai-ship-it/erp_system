"""A positive financial result must originate from persisted PG rows.

An empty fixture, missing tables, ambiguous schema, or NOT_VERIFIED
result is a failed acceptance, never a successful or skipped test.
"""

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup
from app.services.payroll_statutory_service import (
    calculate_payroll_statutory_result,
)
from app.models.payroll_statutory import (
    PayrollStatutoryResultLine,
    PayrollStatutoryLineSource,
)
from sqlalchemy import select, func

from app.services.payroll_regulatory_persisted_allocation import (
    reconcile_persisted_allocations,
)


pytestmark = pytest.mark.asyncio


async def test_positive_persisted_allocation_acceptance(
    employee_engine,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, employee, contract, calculation = (
            await setup(db)
        )

        statutory = await calculate_payroll_statutory_result(
            db,
            company_id=company.id,
            payroll_calculation_id=calculation.id,
            actor_user_id=user.id,
        )

        statutory_line_ids = (
            await db.scalars(
                select(PayrollStatutoryResultLine.id).where(
                    PayrollStatutoryResultLine.company_id == company.id,
                    PayrollStatutoryResultLine.payroll_statutory_result_id
                    == statutory.id,
                )
            )
        ).all()

        assert statutory_line_ids, "Statutory rows absent"

        source_count = await db.scalar(
            select(func.count(PayrollStatutoryLineSource.id)).where(
                PayrollStatutoryLineSource.company_id == company.id,
                PayrollStatutoryLineSource.statutory_line_id.in_(
                    statutory_line_ids
                ),
            )
        )

        print("REAL DATED SOURCE LINKS =", source_count)
        assert source_count > 0, (
            "Real statutory writer persisted no dated source links"
        )

        result = await reconcile_persisted_allocations(
            db,
            company_id=company.id,
            payroll_period_id=calculation.payroll_period_id,
        )

        print(
            "REAL PERSISTED RECONCILIATION STATUS =",
            result["status"],
        )
        print(
            "REAL PERSISTED RECONCILIATION REASON =",
            result.get("reason"),
        )
        print(
            "REAL PERSISTED VERIFIED LINES =",
            result.get("verified_lines"),
        )

        # This gate deliberately rejects NOT_VERIFIED.
        # It is not permitted to replace this assertion with
        # an OR clause accepting NOT_VERIFIED.
        assert result["status"] == "FINANCIAL_LINES_RECONCILED", (
            "Real persisted payroll-to-statutory allocations "
            "have not passed positive reconciliation. "
            f"Evidence: {result}"
        )

        assert result["data_origin"] == "PERSISTED_POSTGRESQL"
        assert result["verified_lines"] > 0
        assert result["line_count"] == result["verified_lines"]
        assert result["reported_total"] == result["allocated_total"]
        assert result["reported_amount_field"] == "base_amount"
        assert all(
            "statutory_tax_amount" in row
            for row in result["lines"]
        )

        assert all(
            row["status"] == "RECONCILED"
            and row["source_count"] > 0
            for row in result["lines"]
        )

        assert result["statutory_mapping_verified"] is False
        assert result["official_generation"] == "BLOCKED"

        print("POSITIVE PERSISTED ALLOCATION = PASS")

        await db.rollback()
