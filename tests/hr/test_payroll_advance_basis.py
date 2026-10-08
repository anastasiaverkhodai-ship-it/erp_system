import os
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_source_locking import sources
from app.schemas.payroll import PayrollPeriodCreate
from app.schemas.time_attendance import AttendanceCreate
from app.services.payroll_period_service import create_payroll_period
from app.services.time_attendance_service import create_attendance
from app.services.payroll_advance_basis_service import derive_payroll_advance_basis, PayrollAdvanceBasisError

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


@pytest.mark.parametrize('rate_case', ['monthly', 'hourly', 'mid_month_change'])
async def test_advance_basis_requires_complete_first_half_and_uses_full_month_norm(employee_engine, rate_case):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, contract, _ = await sources(db)
        from sqlalchemy import select
        from app.models.employee_salary_rate import EmployeeSalaryRate
        from app.services.employee_salary_rate_service import create_salary_rate
        from app.schemas.employee_salary_rate import SalaryRateCreate
        rate = await db.scalar(select(EmployeeSalaryRate).where(EmployeeSalaryRate.employment_contract_id == contract.id))
        if rate_case == 'hourly':
            rate.rate_type = 'hourly'
            rate.amount = Decimal('100')
        elif rate_case == 'mid_month_change':
            rate.effective_to = date(2026, 9, 15)
            await db.flush()
            await create_salary_rate(db, company_id=f['company'], created_by=f['actor'],
                data=SalaryRateCreate(employment_contract_id=contract.id, rate_type='monthly',
                    amount=Decimal('33000'), currency_code='UAH', effective_from=date(2026, 9, 16)))
        await db.flush()
        period = await create_payroll_period(db, company_id=f['company'], created_by=f['actor'],
            data=PayrollPeriodCreate(year=2026, month=9, start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)))
        kwargs = dict(company_id=f['company'], payroll_period_id=period.id,
            employment_contract_id=contract.id, advance_percentage=Decimal('40'))
        with pytest.raises(PayrollAdvanceBasisError, match='Missing attendance'):
            await derive_payroll_advance_basis(db, **kwargs)
        for day in range(1, 16):
            work_date = date(2026, 9, day)
            if work_date.weekday() > 4:
                continue
            # One absence, ten full days. No attendance exists after the 15th.
            present = day != 1
            await create_attendance(db, company_id=f['company'], created_by=f['actor'],
                data=AttendanceCreate(employment_contract_id=contract.id, work_date=work_date,
                    status='present' if present else 'absent',
                    actual_start_at=datetime(2026, 9, day, 9, tzinfo=ZoneInfo('Europe/Kyiv')) if present else None,
                    actual_end_at=datetime(2026, 9, day, 17, tzinfo=ZoneInfo('Europe/Kyiv')) if present else None))
        result = await derive_payroll_advance_basis(db, **kwargs)
        assert result['monthly_norm_minutes'] == 22 * 8 * 60
        base, minimum = {'monthly': ('22000', '10000'), 'hourly': ('17600', '8000'),
                         'mid_month_change': ('27500', '10000')}[rate_case]
        assert result['calculation_base_gross'] == Decimal(base)
        assert result['minimum_gross'] == Decimal(minimum)
        assert result['percentage_gross'] == Decimal(base) * Decimal('.4')
        assert result['recommended_gross'] == max(Decimal(base) * Decimal('.4'), Decimal(minimum))
        assert result['percentage_meets_minimum'] == (rate_case == 'mid_month_change')
        assert len(result['sources'][0]['attendance_ids']) == 11
        assert result['earned_through'] == date(2026, 9, 15)
        with pytest.raises(PayrollAdvanceBasisError):
            await derive_payroll_advance_basis(db, **{**kwargs, 'company_id': 2147483647})
        with pytest.raises(PayrollAdvanceBasisError):
            await derive_payroll_advance_basis(db, **{**kwargs, 'advance_percentage': Decimal('NaN')})
