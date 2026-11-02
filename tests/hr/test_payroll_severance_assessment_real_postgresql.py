"""Direct assessment against real isolated PostgreSQL payroll sources."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_severance_postpayment_assessment import (
    assess_severance_postpayment,
)
from test_payroll_severance_snapshot_service_postgresql import (
    _run_severance_snapshot_service_e2e,
)


@pytest.mark.asyncio
async def test_direct_assessment_of_real_verified_severance():
    async def verify(db, company, user, employee, contract, calc, tax):
        source_lines = list((
            await db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.company_id == company.id,
                    PayrollCalculationLine.payroll_calculation_id
                    == calc.id,
                    PayrollCalculationLine.line_type == "severance",
                )
            )
        ).all())

        assert len(source_lines) == 1
        source_id = source_lines[0].source_termination_severance_id
        assert source_id is not None

        source = await db.scalar(
            select(PayrollTerminationSeverance).where(
                PayrollTerminationSeverance.company_id == company.id,
                PayrollTerminationSeverance.id == source_id,
            )
        )
        assert source is not None

        assessment = await assess_severance_postpayment(
            db,
            company_id=company.id,
            payroll_input_id=calc.payroll_input_id,
        )

        assert assessment.company_id == company.id
        assert assessment.payroll_input_id == calc.payroll_input_id
        assert assessment.latest_source_id == source.id
        assert assessment.last_calculated_source_id == source.id
        assert assessment.latest_gross == Decimal(source.gross_amount)
        assert assessment.original_gross == Decimal(source.gross_amount)
        assert assessment.gross_difference == Decimal("0.00")
        assert assessment.posted_disbursement_count == 0
        assert assessment.posted_disbursement_total == Decimal("0.00")
        assert assessment.classification == "NO_POSTED_PAYMENT"
        assert assessment.payment_authorized is False
        assert assessment.tax_correction_authorized is False
        assert assessment.journal_posting_authorized is False

        # Repeated assessment is stable and has no write side effects.
        repeat = await assess_severance_postpayment(
            db,
            company_id=company.id,
            payroll_input_id=calc.payroll_input_id,
        )
        assert repeat == assessment

        print("DIRECT REAL SEVERANCE ASSESSMENT = PASS")
        print("VERIFIED SOURCE ATTRIBUTION = PASS")
        print("ASSESSMENT IDEMPOTENT READ = PASS")
        print("FINANCIAL EXECUTION AUTHORIZED = NO")

    await _run_severance_snapshot_service_e2e(
        exercise_financial_path=True,
        trusted_policy=True,
        financial_callback=verify,
    )
