"""Real PostgreSQL negative E2E for severance statutory multiline safety."""

import os

import pytest

from sqlalchemy import func, select

from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_severance_payment_service import severance_net

from test_payroll_severance_snapshot_service_postgresql import (
    _run_severance_snapshot_service_e2e,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real isolated PostgreSQL required",
    ),
]


@pytest.mark.parametrize(
    "duplicate_component",
    [
        "personal_income_tax",
        "military_levy",
    ],
)
async def test_real_severance_rejects_duplicate_statutory_component(
    duplicate_component,
):
    async def callback(
        db,
        company,
        user,
        employee,
        contract,
        calc,
        tax,
    ):
        assert calc.company_id == company.id
        assert tax.company_id == company.id
        assert tax.payroll_calculation_id == calc.id

        baseline = await severance_net(
            db,
            company_id=company.id,
            payroll_calculation_id=calc.id,
        )

        assert baseline > 0
        print(
            "SEVERANCE POSITIVE TRUSTED BASELINE = PASS",
            duplicate_component,
        )

        result = await db.scalar(
            select(PayrollStatutoryResult).where(
                PayrollStatutoryResult.company_id == company.id,
                PayrollStatutoryResult.payroll_calculation_id == calc.id,
            )
        )

        assert result is not None

        rows = list(
            (
                await db.scalars(
                    select(PayrollStatutoryResultLine).where(
                        PayrollStatutoryResultLine.company_id == company.id,
                        PayrollStatutoryResultLine.payroll_statutory_result_id
                        == result.id,
                        PayrollStatutoryResultLine.component
                        == duplicate_component,
                    )
                )
            ).all()
        )

        assert len(rows) == 1
        original = rows[0]

        line_numbers = list(
            (
                await db.scalars(
                    select(PayrollStatutoryResultLine.line_no).where(
                        PayrollStatutoryResultLine.company_id == company.id,
                        PayrollStatutoryResultLine.payroll_statutory_result_id
                        == result.id,
                    )
                )
            ).all()
        )

        assert line_numbers

        async def financial_counts():
            return {
                "journals": await db.scalar(
                    select(func.count(JournalEntry.id)).where(
                        JournalEntry.company_id == company.id,
                    )
                ),
                "payments": await db.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.company_id == company.id,
                    )
                ),
            }

        financial_before = await financial_counts()
        net_before = result.net_amount

        savepoint = await db.begin_nested()

        try:
            values = {
                column.key: getattr(original, column.key)
                for column in PayrollStatutoryResultLine.__table__.columns
                if column.key not in (
                    "id",
                    "line_no",
                    "created_at",
                )
            }

            duplicate = PayrollStatutoryResultLine(
                **values,
                line_no=max(line_numbers) + 1,
            )

            db.add(duplicate)
            await db.flush()

            count = await db.scalar(
                select(func.count(PayrollStatutoryResultLine.id)).where(
                    PayrollStatutoryResultLine.company_id == company.id,
                    PayrollStatutoryResultLine.payroll_statutory_result_id
                    == result.id,
                    PayrollStatutoryResultLine.component
                    == duplicate_component,
                )
            )

            assert count == 2

            print(
                "SEVERANCE TWO REAL STORED TAX LINES = PASS",
                duplicate_component,
            )

            with pytest.raises(
                ValueError,
                match="Severance statutory component has multiple lines",
            ):
                await severance_net(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calc.id,
                )

            assert await financial_counts() == financial_before
            assert result.net_amount == net_before

            print(
                "SEVERANCE EXACT MULTILINE REJECTION = PASS",
                duplicate_component,
            )
            print(
                "SEVERANCE FINANCIAL ZERO LEAK = PASS",
                duplicate_component,
            )

        finally:
            if savepoint.is_active:
                await savepoint.rollback()

        restored_count = await db.scalar(
            select(func.count(PayrollStatutoryResultLine.id)).where(
                PayrollStatutoryResultLine.company_id == company.id,
                PayrollStatutoryResultLine.payroll_statutory_result_id
                == result.id,
                PayrollStatutoryResultLine.component
                == duplicate_component,
            )
        )

        assert restored_count == 1
        assert await financial_counts() == financial_before

        restored = await severance_net(
            db,
            company_id=company.id,
            payroll_calculation_id=calc.id,
        )

        assert restored == baseline

        print(
            "SEVERANCE SAVEPOINT RESTORE AND RETRY = PASS",
            duplicate_component,
        )

    await _run_severance_snapshot_service_e2e(
        exercise_financial_path=True,
        trusted_policy=True,
        financial_callback=callback,
    )
