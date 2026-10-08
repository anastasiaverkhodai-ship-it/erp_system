"""A repeated leave request cannot silently acknowledge different money or evidence."""
from datetime import date, datetime, UTC
from decimal import Decimal
import os
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from app.models.leave_request import LeaveRequest
from app.models.payroll import PayrollPeriod, PayrollInput
from app.services import payroll_vacation_service as vacation
from app.services import payroll_sick_leave_service as sick

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='PostgreSQL required')]


@pytest.mark.parametrize('kind', ['vacation', 'sick'])
async def test_leave_retry_and_finalized_period_guard(employee_engine, kind):
    module = vacation if kind == 'vacation' else sick
    calculate = module.calculate_vacation_pay if kind == 'vacation' else module.calculate_sick_leave_pay
    conflict = module.PayrollVacationConflictError if kind == 'vacation' else module.PayrollSickLeaveConflictError
    invalid = module.PayrollVacationDerivationError if kind == 'vacation' else module.PayrollSickLeaveDerivationError
    model = module.PayrollVacationCalculation if kind == 'vacation' else module.PayrollSickLeaveCalculation
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, user, _, contract = await _seed_identity(db)
        def leave(day):
            return LeaveRequest(company_id=company.id, employment_contract_id=contract.id,
                leave_type='annual' if kind == 'vacation' else 'sick',
                start_date=date(2026,9,day), end_date=date(2026,9,day+4), status='approved',
                requested_by=user.id, approved_by=user.id, approved_at=datetime.now(UTC))
        first = leave(15)
        second = leave(25)
        db.add_all([first, second])
        await db.flush()
        args = dict(company_id=company.id, leave_request_id=first.id,
            reference_period_start=date(2025,9,1), reference_period_end=date(2026,8,31),
            eligible_earnings=Decimal('36500'), eligible_days=Decimal('365'),
            rule_code='RETRY', rule_version='1', calculated_by=user.id, sources=[])
        if kind == 'sick':
            args.update(benefit_case_code='ordinary', insurance_service_months=120,
                benefit_percent=Decimal('100'), limited_service_rule_applied=False,
                employer_days=Decimal('5'), insurer_days=Decimal('0'))
        with pytest.raises(invalid, match='finite'):
            await calculate(db, **dict(args, eligible_earnings=Decimal('NaN')))
        with pytest.raises(invalid, match='two decimal'):
            await calculate(db, **dict(args, eligible_days=Decimal('365.001')))
        original = await calculate(db, **args)
        assert (await calculate(db, **args)).id == original.id
        for field, value in [('eligible_earnings', Decimal('73000')), ('eligible_days', Decimal('300')),
            ('reference_period_start', date(2025,10,1)), ('rule_version', '2'),
            ('sources', [dict(source_type='earnings_history', source_id=987,
                              earnings_amount=Decimal('36500'), eligible_days=Decimal('365'))])]:
            with pytest.raises(conflict, match='different'):
                await calculate(db, **dict(args, **{field: value}))
        if kind == 'sick':
            for field, value in [('benefit_percent', Decimal('50')), ('insurance_service_months', 6),
                                 ('limited_service_rule_applied', True), ('employer_days', Decimal('4'))]:
                with pytest.raises(conflict, match='different'):
                    await calculate(db, **dict(args, **{field:value}))
        assert await db.scalar(select(func.count()).select_from(model)) == 1
        period = PayrollPeriod(company_id=company.id, year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='finalized',
            created_by=user.id, finalized_by=user.id, finalized_at=datetime.now(UTC))
        db.add(period)
        await db.flush()
        db.add(PayrollInput(company_id=company.id, payroll_period_id=period.id,
            employment_contract_id=contract.id, scheduled_minutes=0, worked_minutes=0,
            leave_days=0, sick_days=0, manual_adjustment_amount=0, created_by=user.id))
        await db.flush()
        assert (await calculate(db, **args)).id == original.id
        with pytest.raises(conflict, match='finalized'):
            await calculate(db, **dict(args, leave_request_id=second.id))
        assert await db.scalar(select(func.count()).select_from(model)) == 1
