import os
from datetime import date
from decimal import Decimal as D
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_dated_employee_statutory_postgresql import setup
from app.models.user import User
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_statutory import PayrollStatutoryResult,PayrollStatutoryLineSource
from app.schemas.payroll_income_dates import IncomeDateRequestCreate
from app.services.payroll_income_dates_service import request_income_dates,approve_income_dates
from app.services.payroll_statutory_service import calculate_payroll_statutory_result,PayrollStatutoryValidationError
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='PostgreSQL required')]

@pytest.mark.parametrize('amount,usc,net',[(1000,'3347.66',17710),(-1000,'3070.74',16170)])
async def test_reviewed_signed_adjustment_reaches_monthly_tax(employee_engine,amount,usc,net):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract,calc=await setup(db,adjustment=amount)
        line=await db.scalar(select(PayrollCalculationLine).where(PayrollCalculationLine.payroll_calculation_id==calc.id,
            PayrollCalculationLine.line_type=='manual_adjustment'))
        reviewer=User(email=uuid4().hex+'@example.invalid',password_hash='unused',first_name='Review',last_name='User',is_active=True)
        db.add(reviewer);await db.flush()
        with pytest.raises(PayrollStatutoryValidationError,match='approved earning dates'):
            await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        assert await db.scalar(select(func.count(PayrollStatutoryResult.id)).where(PayrollStatutoryResult.company_id==company.id))==0
        data=IncomeDateRequestCreate(line_id=line.id,request_key='allocation',document_reference='Confirmed accountant allocation',
            dates=[dict(income_date=date(2026,9,10),amount=D(amount)*D('.4')),
                   dict(income_date=date(2026,9,20),amount=D(amount)*D('.6'))])
        request=await request_income_dates(db,company_id=company.id,actor_id=user.id,data=data)
        assert (await request_income_dates(db,company_id=company.id,actor_id=user.id,data=data)).id==request.id
        with pytest.raises(ValueError,match='Independent'):
            await approve_income_dates(db,company_id=company.id,request_id=request.id,actor_id=user.id)
        approved=await approve_income_dates(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        result=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        assert result.employer_contribution_amount==D(usc)
        assert result.net_amount==net
        assert (await approve_income_dates(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)).id==approved.id
        sources=list((await db.scalars(select(PayrollStatutoryLineSource).where(
            PayrollStatutoryLineSource.payroll_calculation_line_id==line.id))).all())
        assert sum(item.source_amount for item in sources)==D(amount)*3
        with pytest.raises(ValueError,match='already assessed'):
            await request_income_dates(db,company_id=company.id,actor_id=user.id,
                data=data.model_copy(update={'request_key':'late-change'}))
