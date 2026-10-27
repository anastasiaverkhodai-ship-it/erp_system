
"""Full-service internal-secondary sick calculation on real PostgreSQL."""
import os
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_insurance_evidence import insurance_setup
from test_payroll_vacation_postgresql import _required_values

from app.models.employment_contract import EmploymentContract
from app.models.leave_request import LeaveRequest
from app.models.payroll_opening import PayrollEarningsHistory
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.schemas.payroll_opening import PayrollEarningsHistoryInput

from app.services.payroll_opening_service import import_earnings_history
from app.services.payroll_insurance_evidence_service import (
    review_insurance_evidence,
)
from app.services.payroll_sick_history_service import (
    calculate_sick_from_history,
    PayrollSickLeaveDerivationError,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="PostgreSQL required",
    ),
]


async def prepare(db):
    company, user, primary, original_leave, reviewer, _, evidence = (
        await insurance_setup(db)
    )

    primary.employment_kind = "primary"

    secondary = EmploymentContract(
        **_required_values(
            EmploymentContract,
            {
                "company_id": company.id,
                "employee_id": primary.employee_id,
                "contract_number": "SECONDARY-" + uuid4().hex[:12],
                "contract_type": "standard",
                "work_arrangement": "part_time",
                "employment_kind": "internal_secondary",
                "start_date": date(2025, 1, 1),
                "end_date": None,
                "status": "active",
                "created_by": user.id,
            },
        )
    )
    db.add(secondary)
    await db.flush()

    original_leave.employment_contract_id = secondary.id

    primary_histories = list(
        (await db.scalars(
            select(PayrollEarningsHistory).where(
                PayrollEarningsHistory.company_id == company.id,
                PayrollEarningsHistory.employment_contract_id == primary.id,
            ).order_by(PayrollEarningsHistory.month)
        )).all()
    )
    assert len(primary_histories) == 12

    for history in primary_histories:
        days = history.sick_days
        assert days > 0

        await import_earnings_history(
            db,
            company_id=company.id,
            contract_id=secondary.id,
            data=PayrollEarningsHistoryInput(
                month=history.month,
                cutover_date=date(2026, 9, 1),
                gross_amount=D(days) * D(20),
                vacation_earnings=D(days) * D(20),
                sick_earnings=D(days) * D(20),
                vacation_days=days,
                sick_days=days,
                source_reference=(
                    "Reviewed secondary payroll register "
                    + history.month.isoformat()
                ),
            ),
            created_by=user.id,
        )

    await review_insurance_evidence(
        db,
        company_id=company.id,
        evidence_id=evidence.id,
        reviewed_by=reviewer.id,
        approved=True,
    )

    args = {
        "company_id": company.id,
        "leave_request_id": original_leave.id,
        "insurance_evidence_id": evidence.id,
        "calculated_by": user.id,
    }

    return company, primary, secondary, primary_histories, args


async def test_internal_secondary_real_calculation_and_idempotency(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        calculation = await calculate_sick_from_history(
            db, **args
        )

        assert calculation.employment_contract_id == secondary.id
        assert calculation.company_id == company.id
        assert calculation.eligible_earnings > 0
        assert calculation.eligible_days > 0
        assert calculation.sick_pay_amount > 0

        assessment = calculation.insurance_assessment

        assert (
            assessment["combined_basis_scope"]
            == "same_company_imported_internal_secondary"
        )

        months = assessment["combined_insurance_months"]
        assert len(months) == 12
        assert all(m["contract_count"] == 2 for m in months)

        # The secondary calculation must not include primary earnings
        # in its own earnings numerator.
        secondary_total = D(0)
        for row in months:
            assert D(row["combined_earnings"]) <= D(row["maximum"])

        secondary_histories = list(
            (await db.scalars(
                select(PayrollEarningsHistory).where(
                    PayrollEarningsHistory.company_id == company.id,
                    PayrollEarningsHistory.employment_contract_id
                    == secondary.id,
                )
            )).all()
        )
        secondary_total = sum(
            (D(row.sick_earnings) for row in secondary_histories),
            D(0),
        )
        assert calculation.eligible_earnings == secondary_total

        repeated = await calculate_sick_from_history(
            db, **args
        )
        assert repeated.id == calculation.id

        count = await db.scalar(
            select(func.count()).select_from(
                PayrollSickLeaveCalculation
            ).where(
                PayrollSickLeaveCalculation.company_id == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == secondary.id,
            )
        )
        assert count == 1


async def test_internal_secondary_refuses_missing_primary_month(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, primary, secondary, histories, args = (
            await prepare(db)
        )

        # Remove one required primary insurance month.
        await db.delete(histories[0])
        await db.flush()

        with pytest.raises(
            PayrollSickLeaveDerivationError,
            match="Incomplete combined insurance history",
        ):
            await calculate_sick_from_history(db, **args)

        count = await db.scalar(
            select(func.count()).select_from(
                PayrollSickLeaveCalculation
            ).where(
                PayrollSickLeaveCalculation.company_id == company.id,
                PayrollSickLeaveCalculation.employment_contract_id
                == secondary.id,
            )
        )
        assert count == 0
