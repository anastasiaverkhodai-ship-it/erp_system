"""Sick-pay history uses assessed contribution earnings and excludes sick days."""
import os
from datetime import date,datetime,UTC
from decimal import Decimal as D
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_average_history import seed
from test_payroll_average_classification import month
from app.models.leave_request import LeaveRequest
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_supplement import PayrollSupplement
from app.models.payroll_statutory import PayrollStatutoryComponent as Component,PayrollStatutoryResultLine
from app.services.payroll_statutory_service import create_statutory_rate,calculate_payroll_statutory_result
from app.services.payroll_average_history_service import derive_leave_average_history,PayrollAverageHistoryError
from app.services.payroll_vacation_service import calculate_vacation_pay
from app.services.payroll_sick_leave_service import calculate_sick_leave_pay
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]



async def test_sick_history_rejects_two_real_usc_lines(employee_engine):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,leave,_=await seed(db,erp_month=True)
        contract.employment_kind='primary'
        leave.leave_type='sick'
        paid=[]
        for kind,start,end in [('annual',1,3),('sick',4,5),('unpaid',6,7)]:
            item=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type=kind,
                start_date=date(2026,8,start),end_date=date(2026,8,end),status='approved',
                requested_by=user.id,approved_by=user.id,approved_at=datetime.now(UTC))
            db.add(item);await db.flush();paid.append(item)
        common=dict(company_id=company.id,reference_period_start=date(2025,8,1),reference_period_end=date(2026,7,31),
            eligible_earnings=D(36500),eligible_days=D(365),rule_code='FIXTURE',rule_version='1',calculated_by=user.id)
        vacation=await calculate_vacation_pay(db,leave_request_id=paid[0].id,**common)
        sick=await calculate_sick_leave_pay(db,leave_request_id=paid[1].id,**common,
            benefit_case_code='ORDINARY_TEMPORARY_INCAPACITY',insurance_service_months=120,
            benefit_percent=D(100),limited_service_rule_applied=False,employer_days=D(2),insurer_days=D(0))
        source,calc=await month(db,company,user,contract,4200)
        db.add_all([
            PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=2,
                line_type='vacation_pay',quantity=3,rate=100,amount=300,currency_code='UAH',source_vacation_calculation_id=vacation.id),
            PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=3,
                line_type='sick_pay',quantity=2,rate=100,amount=200,currency_code='UAH',source_sick_leave_calculation_id=sick.id),
        ])
        for n,kind,amount in [(4,'bonus',500),(5,'holiday_bonus',100)]:
            item=PayrollSupplement(company_id=company.id,payroll_input_id=source.id,kind=kind,work_date=date(2026,8,10),
                minutes=0,coefficient=0,amount=amount,source_reference='Bonus order',request_key=kind,
                request_fingerprint=kind,created_by=user.id)
            db.add(item);await db.flush()
            db.add(PayrollCalculationLine(company_id=company.id,payroll_calculation_id=calc.id,line_no=n,
                line_type='supplement',quantity=1,rate=amount,amount=amount,currency_code='UAH',source_supplement_id=item.id))
        await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id)
        with pytest.raises(PayrollAverageHistoryError,match='assessed USC'):
            await derive_leave_average_history(db,**args)
        for component,rate in [(Component.PERSONAL_INCOME_TAX,D('.18')),(Component.MILITARY_LEVY,D('.05')),
                               (Component.UNIFIED_SOCIAL_CONTRIBUTION,D('.22'))]:
            await create_statutory_rate(db,company_id=company.id,component=component,rate=rate,
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
        tax=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)

        from app.models.payroll_statutory import (
            PayrollStatutoryResultLine as USCLine,
        )
        from sqlalchemy import func

        original = await db.scalar(
            select(USCLine).where(
                USCLine.company_id == company.id,
                USCLine.payroll_statutory_result_id == tax.id,
                USCLine.component ==
                Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            )
        )
        assert original is not None

        values = {
            column.key: getattr(original, column.key)
            for column in USCLine.__table__.columns
            if column.key not in (
                "id", "line_no", "created_at",
            )
        }

        numbers = list(
            (
                await db.scalars(
                    select(USCLine.line_no).where(
                        USCLine.company_id == company.id,
                        USCLine.payroll_statutory_result_id == tax.id,
                    )
                )
            ).all()
        )

        assert numbers

        db.add(
            USCLine(
                **values,
                line_no=max(numbers) + 1,
            )
        )
        await db.flush()

        count = await db.scalar(
            select(func.count(USCLine.id)).where(
                USCLine.company_id == company.id,
                USCLine.payroll_statutory_result_id == tax.id,
                USCLine.component ==
                Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            )
        )
        assert count == 2

        print("REAL STORED SICK HISTORY USC LINES = 2 PASS")

        with pytest.raises(
            PayrollAverageHistoryError,
            match="Multiple dated USC lines require attributed",
        ):
            await derive_leave_average_history(db, **args)

        print("SICK HISTORY MULTILINE GUARD = PASS")

        count_after = await db.scalar(
            select(func.count(USCLine.id)).where(
                USCLine.company_id == company.id,
                USCLine.payroll_statutory_result_id == tax.id,
                USCLine.component ==
                Component.UNIFIED_SOCIAL_CONTRIBUTION.value,
            )
        )

        assert count_after == 2

        print("SICK HISTORY SOURCE ROWS PRESERVED = PASS")

        await db.rollback()
