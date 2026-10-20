"""Real source services: termination -> compensation -> payroll -> taxes -> bank."""
from sqlalchemy import select, func
import os
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources

pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1', reason='Isolated PostgreSQL')]


async def test_combined_salary_leave_severance_final_settlement(employee_engine):
    from app.models.employee import Employee
    from app.models.payroll_opening import PayrollEarningsHistory
    from app.models.account import Account
    from app.models.accounting_period import AccountingPeriod
    from app.models.bank_account import BankAccount
    from app.models.payroll import PayrollCalculationLine
    from app.schemas.employment_event import EmploymentEventCreate
    from app.schemas.payroll import PayrollPeriodCreate, PayrollInputCreate
    from app.schemas.time_attendance import AttendanceCreate
    from app.schemas.payroll_leave_balance import LeaveAccountCreate, LeaveAccrualCreate
    from app.schemas.payroll_leave_compensation import LeaveCompensationCreate
    from app.services.employment_event_service import create_employment_event
    from app.services.payroll_period_service import create_payroll_period, finalize_payroll_period
    from app.services.time_attendance_service import create_attendance
    from app.services.payroll_input_service import create_payroll_input
    from app.services.payroll_calculation_service import calculate_payroll_input
    from app.services.payroll_leave_balance_service import create_leave_account, accrue_leave_days
    from app.services.payroll_leave_compensation_service import create_leave_compensation
    from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
    from app.models.payroll_statutory import PayrollStatutoryComponent as Component
    from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
    from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f,c,_=await sources(db)
        company,actor=f['company'],f['actor']
        employee=await db.get(Employee,c.employee_id)
        employee.payment_iban='UA123456789012345678901234567'
        event=await create_employment_event(db,company_id=company,contract_id=c.id,created_by=actor,
            data=EmploymentEventCreate(event_type='termination',effective_date=date(2026,9,30),
                order_number='TERM-FINAL',order_date=date(2026,9,30),reason='Documented termination',request_key='termination'))
        for month in range(2,9):
            days=monthrange(2026,month)[1]
            db.add(PayrollEarningsHistory(company_id=company,employment_contract_id=c.id,
                month=date(2026,month,1),cutover_date=date(2026,9,1),gross_amount=D(days)*100,
                vacation_earnings=D(days)*100,sick_earnings=D(days)*100,vacation_days=days,sick_days=days,
                source_reference='Verified preceding payroll',request_fingerprint=str(month),created_by=actor))
        period=await create_payroll_period(db,company_id=company,created_by=actor,
            data=PayrollPeriodCreate(year=2026,month=9,start_date=date(2026,9,1),end_date=date(2026,9,30)))
        for day in range(1,31):
            if date(2026,9,day).weekday()>4:
                continue
            await create_attendance(db,company_id=company,created_by=actor,data=AttendanceCreate(
                employment_contract_id=c.id,work_date=date(2026,9,day),status='present',
                actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        inputs=await create_payroll_input(db,company_id=company,payroll_period_id=period.id,
            created_by=actor,data=PayrollInputCreate(employment_contract_id=c.id))
        account=await create_leave_account(db,company_id=company,actor_id=actor,data=LeaveAccountCreate(
            employment_contract_id=c.id,leave_kind='annual',entitlement_code='basic',
            period_start=date(2026,1,10),period_end=date(2027,1,9),
            segments=[dict(start=date(2026,1,10),end=date(2027,1,9),annual_days=24)],
            source_reference='First employment year',request_key='rights'))
        await accrue_leave_days(db,company_id=company,account_id=account.id,actor_id=actor,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Verified employment service',request_key='accrue'))
        compensation=await create_leave_compensation(db,company_id=company,actor_id=actor,
            data=LeaveCompensationCreate(termination_event_id=event.id,payroll_input_id=inputs.id,
                request_key='compensate',entitlement_review_reference='All leave rights reviewed'))
        assert compensation.gross_amount==1700

        # Add immutable severance evidence to the SAME payroll input.
        # Leave compensation was already derived by production services.
        from app.models.payroll_termination_severance import (
            PayrollTerminationSeverance,
        )

        event.request_payload = {
            **(event.request_payload or {}),
            "termination_basis": {
                "ground": "redundancy",
                "labour_code_provision": "40.1",
                "source_reference": "Verified termination order",
            },
        }
        await db.flush()

        severance_source = PayrollTerminationSeverance(
            company_id=company,
            payroll_input_id=inputs.id,
            employment_contract_id=c.id,
            termination_event_id=event.id,
            revision=1,
            supersedes_id=None,
            request_key="combined-settlement-severance",
            termination_date=date(2026, 9, 30),
            order_number=event.order_number,
            policy_ground="redundancy",
            policy_category="average_months",
            labour_code_provision="40.1",
            source_reference="Verified termination order",
            multiplier=1,
            basis_as_of=date(2026, 9, 30),
            basis_reference="Verified average monthly earnings",
            base_amount=D("12000.00"),
            gross_amount=D("12000.00"),
            currency_code="UAH",
            created_by=actor,
        )
        db.add(severance_source)
        await db.flush()

        await finalize_payroll_period(db,company_id=company,payroll_period_id=period.id,finalized_by=actor)
        calc=await calculate_payroll_input(db,company_id=company,payroll_input_id=inputs.id,calculated_by=actor)
        assert calc.gross_amount==D('35700.00')
        assert (await calculate_payroll_input(db,company_id=company,payroll_input_id=inputs.id,calculated_by=actor)).id==calc.id
        lines=list((await db.scalars(select(PayrollCalculationLine).where(
            PayrollCalculationLine.payroll_calculation_id==calc.id,PayrollCalculationLine.line_type=='leave_compensation'))).all())
        assert len(lines)==1 and lines[0].amount==1700 and lines[0].source_leave_compensation_line_id

        import pytest
        from unittest.mock import patch
        from app.models.payroll_disbursement import PayrollDisbursement
        from app.models.journal_entry import JournalEntry
        from app.services.payroll_statutory_service import (
            PayrollStatutoryValidationError,
        )
        from app.services.payroll_disbursement_service import (
            PayrollDisbursementSourceStateError,
        )

        all_lines = list(
            (
                await db.scalars(
                    select(PayrollCalculationLine).where(
                        PayrollCalculationLine.company_id == company,
                        PayrollCalculationLine.payroll_calculation_id == calc.id,
                    )
                )
            ).all()
        )

        leave_lines = [
            x for x in all_lines
            if x.line_type == "leave_compensation"
        ]
        severance_lines = [
            x for x in all_lines
            if x.line_type == "severance"
        ]

        assert len(leave_lines) == 1
        assert len(severance_lines) == 1
        assert D(leave_lines[0].amount) == D("1700.00")
        assert D(severance_lines[0].amount) == D("12000.00")

        assert leave_lines[0].source_leave_compensation_line_id
        assert (
            severance_lines[0].source_termination_severance_id
            == severance_source.id
        )

        total = sum(
            (D(line.amount) for line in all_lines),
            D("0.00"),
        )
        assert total == D(calc.gross_amount) == D("35700.00")

        # Production payroll is idempotent and cannot duplicate
        # either documented termination benefit.
        repeated = await calculate_payroll_input(
            db,
            company_id=company,
            payroll_input_id=inputs.id,
            calculated_by=actor,
        )
        assert repeated.id == calc.id

        repeated_lines = list(
            (
                await db.scalars(
                    select(PayrollCalculationLine).where(
                        PayrollCalculationLine.company_id == company,
                        PayrollCalculationLine.payroll_calculation_id == calc.id,
                    )
                )
            ).all()
        )
        assert len(repeated_lines) == len(all_lines)

        assert sum(
            x.line_type == "leave_compensation"
            for x in repeated_lines
        ) == 1
        assert sum(
            x.line_type == "severance"
            for x in repeated_lines
        ) == 1

        # No statute-classification inference or unauthorized
        # final payment is permitted.
        statutory_before = await db.scalar(
            select(func.count()).select_from(
                __import__(
                    "app.models.payroll_statutory",
                    fromlist=["PayrollStatutoryResult"],
                ).PayrollStatutoryResult
            ).where(
                __import__(
                    "app.models.payroll_statutory",
                    fromlist=["PayrollStatutoryResult"],
                ).PayrollStatutoryResult.payroll_calculation_id == calc.id
            )
        )

        payment_before = await db.scalar(
            select(func.count(PayrollDisbursement.id)).where(
                PayrollDisbursement.company_id == company,
                PayrollDisbursement.payroll_calculation_id == calc.id,
            )
        )

        journal_before = await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company,
            )
        )

        with pytest.raises(
            PayrollStatutoryValidationError,
            match="Severance statutory classification",
        ):
            await calculate_payroll_statutory_result(
                db,
                company_id=company,
                payroll_calculation_id=calc.id,
                actor_user_id=actor,
            )

        # Early-return bypass is also blocked.
        with patch(
            "app.services.payroll_disbursement_service."
            "get_payroll_disbursement",
            side_effect=AssertionError(
                "Existing payment lookup must not be reached"
            ),
        ) as lookup:
            with pytest.raises(
                PayrollDisbursementSourceStateError,
                match="Severance statutory classification",
            ):
                await create_payroll_disbursement(
                    db,
                    company_id=company,
                    payroll_calculation_id=calc.id,
                    bank_account_id=1,
                    payment_date=date(2026, 9, 30),
                    created_by=actor,
                )
            lookup.assert_not_called()

        assert await db.scalar(
            select(func.count(PayrollDisbursement.id)).where(
                PayrollDisbursement.company_id == company,
                PayrollDisbursement.payroll_calculation_id == calc.id,
            )
        ) == payment_before

        assert await db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company,
            )
        ) == journal_before

        print(
            "COMBINED SETTLEMENT COMPONENTS =",
            {
                x.line_type: str(x.amount)
                for x in repeated_lines
            },
            flush=True,
        )
        print("SALARY + LEAVE + SEVERANCE = ONE CALCULATION", flush=True)
        print("LEAVE COMPENSATION = 1700.00 UAH / ONCE", flush=True)
        print("SEVERANCE = 12000.00 UAH / ONCE", flush=True)
        print("GROSS = 35700.00 UAH", flush=True)
        print("PAYROLL RETRY = IDEMPOTENT", flush=True)
        print("STATUTORY / PAYMENT = FAIL-CLOSED", flush=True)
        print("FINANCIAL WRITES = 0", flush=True)
