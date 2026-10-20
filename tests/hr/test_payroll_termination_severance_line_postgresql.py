"""Real PostgreSQL integration: severance -> payroll line -> tax guard."""

import os
import re
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.employment_event import EmploymentEvent
from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollPeriod,
)
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.services.payroll_statutory_service import (
    PayrollStatutoryValidationError,
    calculate_payroll_statutory_result,
)
from app.services.payroll_termination_severance_lines import (
    derive_severance_payroll_lines,
    require_matching_severance_source_for_existing,
    SeveranceLineError,
)
from test_payroll_vacation_postgresql import _seed_identity


pytestmark = pytest.mark.asyncio


def isolated_database_url():
    assert os.getenv("RUN_POSTGRES_E2E") == "1"

    url = make_url(str(settings.database_url))

    assert re.fullmatch(
        r"erp_r3_copy_[0-9a-f]{16}",
        url.database or "",
    ), "Refusing non-isolated database"

    assert url.host in ("localhost", "127.0.0.1", "::1")

    return str(settings.database_url)


async def test_real_postgresql_severance_payroll_line():
    engine = create_async_engine(
        isolated_database_url(),
        pool_pre_ping=True,
    )

    token = uuid4().hex[:12]

    try:
        async with engine.connect() as conn:
            revision = await conn.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert revision == "13d4a7b8c041"

            constraints = {
                name: definition
                for name, definition in (
                    await conn.execute(text("""
                        SELECT conname, pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conrelid =
                            'payroll_calculation_lines'::regclass
                          AND conname IN (
                            'fk_pcl_severance',
                            'ck_pcl_severance_source',
                            'ck_payroll_calculation_lines_type',
                            'ck_payroll_calculation_lines_rate',
                            'ck_payroll_calculation_lines_amount'
                          )
                    """))
                ).all()
            }

            assert len(constraints) == 5
            assert "severance" in constraints[
                "ck_payroll_calculation_lines_type"
            ]

        async with AsyncSession(
            engine, expire_on_commit=False
        ) as db:
            company, user, employee, contract = (
                await _seed_identity(db)
            )

            contract.status = "ended"
            contract.end_date = date(2026, 9, 30)
            await db.flush()

            period = PayrollPeriod(
                company_id=company.id,
                year=2026,
                month=9,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
                status="draft",
                created_by=user.id,
            )
            db.add(period)
            await db.flush()

            payroll_input = PayrollInput(
                company_id=company.id,
                payroll_period_id=period.id,
                employment_contract_id=contract.id,
                created_by=user.id,
            )

            event = EmploymentEvent(
                company_id=company.id,
                employment_contract_id=contract.id,
                event_type="termination",
                effective_date=date(2026, 9, 30),
                order_number=f"TERM-{token}",
                order_date=date(2026, 9, 30),
                reason="Documented redundancy",
                request_key=f"term-{token}",
                before_state={},
                after_state={},
                request_payload={
                    "termination_basis": {
                        "ground": "redundancy",
                        "labour_code_provision": "40.1",
                        "source_reference": "Verified termination order",
                    }
                },
                created_by=user.id,
            )
            db.add_all([payroll_input, event])
            await db.flush()

            source = PayrollTerminationSeverance(
                company_id=company.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                termination_event_id=event.id,
                revision=1,
                request_key=f"severance-{token}",
                termination_date=date(2026, 9, 30),
                order_number=event.order_number,
                policy_ground="redundancy",
                policy_category="average_months",
                labour_code_provision="40.1",
                source_reference="Verified termination order",
                multiplier=1,
                basis_as_of=date(2026, 9, 30),
                basis_reference="Verified average monthly earnings",
                base_amount=Decimal("12000.00"),
                gross_amount=Decimal("12000.00"),
                currency_code="UAH",
                created_by=user.id,
            )
            db.add(source)
            await db.flush()

            derived = await derive_severance_payroll_lines(
                db,
                company_id=company.id,
                payroll_input=payroll_input,
                period=period,
            )

            assert len(derived) == 1
            assert derived[0]["line_type"] == "severance"
            assert derived[0]["amount"] == Decimal("12000.00")
            assert derived[0][
                "source_termination_severance_id"
            ] == source.id

            calculation = PayrollCalculation(
                company_id=company.id,
                payroll_period_id=period.id,
                payroll_input_id=payroll_input.id,
                employment_contract_id=contract.id,
                revision=1,
                currency_code="UAH",
                gross_amount=Decimal("12000.00"),
                calculated_by=user.id,
            )
            db.add(calculation)
            await db.flush()

            line = PayrollCalculationLine(
                company_id=company.id,
                payroll_calculation_id=calculation.id,
                line_no=1,
                **derived[0],
            )
            db.add(line)
            await db.flush()

            await require_matching_severance_source_for_existing(
                db,
                company_id=company.id,
                payroll_input_id=payroll_input.id,
                calculation_id=calculation.id,
            )

            with pytest.raises(
                PayrollStatutoryValidationError,
                match="Severance statutory classification",
            ):
                await calculate_payroll_statutory_result(
                    db,
                    company_id=company.id,
                    payroll_calculation_id=calculation.id,
                    actor_user_id=user.id,
                )

            invalid_cases = (
                {"source_termination_severance_id": source.id + 999999},
                {"line_type": "salary"},
                {"amount": Decimal("-1.00")},
            )

            for i, changes in enumerate(invalid_cases):
                values = dict(derived[0])
                values.update(changes)

                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        db.add(PayrollCalculationLine(
                            company_id=company.id,
                            payroll_calculation_id=calculation.id,
                            line_no=10 + i,
                            **values,
                        ))
                        await db.flush()

            original = await db.get(
                PayrollCalculationLine, line.id
            )
            assert original is not None
            assert original.amount == Decimal("12000.00")

            await db.commit()

        async with AsyncSession(engine) as db:
            count = await db.scalar(
                select(func.count(PayrollCalculationLine.id))
                .where(
                    PayrollCalculationLine.company_id == company.id,
                    PayrollCalculationLine.payroll_calculation_id
                    == calculation.id,
                    PayrollCalculationLine.line_type == "severance",
                )
            )
            assert count == 1

        print("REAL MIGRATION c041 = PASS")
        print("DOCUMENTED SEVERANCE SOURCE = PASS")
        print("PAYROLL CALCULATION LINE = PASS")
        print("COMPOSITE FK + CHECK = PASS")
        print("STATUTORY MISCLASSIFICATION GUARD = PASS")
        print("HISTORICAL SOURCE MATCH = PASS")
        print("POSTGRESQL SKIPS = 0")

    finally:
        await engine.dispose()
