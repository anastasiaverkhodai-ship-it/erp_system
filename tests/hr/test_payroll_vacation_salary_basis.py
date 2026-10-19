import os
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.leave_request import LeaveRequest
from app.models.payroll_opening import PayrollEarningsHistory
from app.schemas.payroll_leave_compensation import CompensationSalarySubstitution
from app.services.payroll_average_history_service import calculate_vacation_from_history, derive_leave_average_history, PayrollAverageHistoryError
from app.services.payroll_vacation_service import list_vacation_calculation_sources, PayrollVacationConflictError
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


@pytest.mark.parametrize('kind',['annual','additional','social'])
@pytest.mark.parametrize('short_service',[True,False])
async def test_vacation_salary_substitution_preserves_documented_basis(employee_engine,kind,short_service):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2026,9,1) if short_service else date(2025,1,1)
        db.add(EmployeeSalaryRate(company_id=company.id,employment_contract_id=contract.id,
            rate_type='monthly',amount=3000,currency_code='UAH',effective_from=contract.start_date))
        leave=LeaveRequest(company_id=company.id,employment_contract_id=contract.id,leave_type=kind,
            start_date=date(2026,9,21),end_date=date(2026,9,25),status='approved',requested_by=user.id,approved_by=user.id,approved_at=datetime(2026,9,20,tzinfo=timezone.utc))
        db.add(leave)
        if not short_service:
            for i in range(12):
                month=date(2025+(8+i)//12,(8+i)%12+1,1)
                db.add(PayrollEarningsHistory(company_id=company.id,employment_contract_id=contract.id,
                    month=month,cutover_date=date(2026,9,1),gross_amount=0,vacation_earnings=0,sick_earnings=0,
                    vacation_days=0,sick_days=0,source_reference='Confirmed unpaid period',request_fingerprint=str(i),created_by=user.id))
        await db.flush()
        args=dict(company_id=company.id,leave_request_id=leave.id)
        with pytest.raises(PayrollAverageHistoryError,match='documented employment fraction'):
            await derive_leave_average_history(db,**args)
        context=CompensationSalarySubstitution(employment_fraction=D('.5'),source_reference='Half-time employment agreement')
        basis=await derive_leave_average_history(db,**args,salary_substitution=context)
        assert basis['eligible_earnings']==51882 and basis['eligible_days']==365
        result=await calculate_vacation_from_history(db,**args,calculated_by=user.id,salary_substitution=context)
        assert result.vacation_pay_amount==D('710.71')
        assert (await calculate_vacation_from_history(db,**args,calculated_by=user.id,salary_substitution=context)).id==result.id
        with pytest.raises(PayrollVacationConflictError,match='different salary substitution'):
            await calculate_vacation_from_history(db,**args,calculated_by=user.id,
                salary_substitution=context.model_copy(update={'employment_fraction':D(1)}))
        saved=await list_vacation_calculation_sources(db,company_id=company.id,vacation_calculation_id=result.id)
        assert len(saved)==1 and saved[0].source_type=='salary_substitution'
        assert saved[0].basis_snapshot['request']==context.model_dump(mode='json')
        assert saved[0].basis_snapshot['salary_amount']=='3000.00'
        assert len(saved[0].basis_snapshot['zero_earnings_evidence'])==(0 if short_service else 12)
        await db.commit()


@pytest.mark.parametrize('history', ['missing', 'positive'])
async def test_salary_substitution_cannot_hide_actual_or_missing_history(employee_engine, history):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, employee, contract = await _seed_identity(db)
        contract.start_date = date(2026, 8, 1)
        leave = LeaveRequest(
            company_id=company.id, employment_contract_id=contract.id,
            leave_type='annual', start_date=date(2026, 9, 21), end_date=date(2026, 9, 25),
            status='approved', requested_by=user.id, approved_by=user.id,
            approved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        db.add(leave)
        if history == 'positive':
            db.add(PayrollEarningsHistory(
                company_id=company.id, employment_contract_id=contract.id,
                month=date(2026, 8, 1), cutover_date=date(2026, 9, 1),
                gross_amount=3000, vacation_earnings=3000, sick_earnings=3000,
                vacation_days=31, sick_days=31,
                source_reference='Verified August register',
                request_fingerprint='salary-substitution-guard', created_by=user.id,
            ))
        await db.flush()
        context = CompensationSalarySubstitution(
            employment_fraction=D('.5'), source_reference='Half-time employment agreement')
        message = 'Incomplete earnings history' if history == 'missing' else 'cannot replace positive'
        with pytest.raises(PayrollAverageHistoryError, match=message):
            await derive_leave_average_history(
                db, company_id=company.id, leave_request_id=leave.id,
                salary_substitution=context)
