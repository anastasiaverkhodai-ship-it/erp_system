from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.deps import get_current_user
from app.core.database import get_db
from app.api.v1 import payroll_accounting, payroll_disbursements
from app.schemas.time_attendance import EmploymentScheduleAssignmentCreate
from app.services import time_attendance_service as attendance
from app.services.payroll_calculation_service import (
    _derive_monthly_salary_amount, PayrollCalculationDerivationError,
)


@pytest.mark.parametrize('worked,expected', [(9600,'20000.00'),(4800,'10000.00'),(0,'0.00')])
def test_monthly_pay_uses_worked_time(worked, expected):
    amount = _derive_monthly_salary_amount(period=None,
        payroll_input=SimpleNamespace(scheduled_minutes=9600,monthly_norm_minutes=9600),
        salary_slice=SimpleNamespace(salary_rate_amount=Decimal('20000'),
            scheduled_minutes=9600,worked_minutes=worked))
    assert amount == Decimal(expected)


def test_monthly_overtime_requires_explicit_earning():
    with pytest.raises(PayrollCalculationDerivationError, match='overtime'):
        _derive_monthly_salary_amount(period=None,
            payroll_input=SimpleNamespace(scheduled_minutes=9600,monthly_norm_minutes=9600),
            salary_slice=SimpleNamespace(salary_rate_amount=Decimal('20000'),
                scheduled_minutes=9600,worked_minutes=9700))


@pytest.mark.asyncio
async def test_open_ended_assignment_reaches_overlap_validation():
    db=AsyncMock()
    # Company exists, no finalized payroll, overlapping assignment exists.
    db.scalar.side_effect=[1, None, 17]
    with patch.object(attendance, '_require_contract', AsyncMock(return_value=
            SimpleNamespace(start_date=date(2026,1,1),end_date=None,status='active'))), \
         patch.object(attendance, '_require_schedule', AsyncMock(return_value=
            SimpleNamespace(id=1,is_active=True))):
        with pytest.raises(attendance.TimeAttendanceConflictError, match='overlaps'):
            await attendance.create_schedule_assignment(db,company_id=1,contract_id=1,
                created_by=1,data=EmploymentScheduleAssignmentCreate(
                    work_schedule_id=1,effective_from=date(2026,1,1)))


@pytest.mark.asyncio
@pytest.mark.parametrize('router,path', [
    (payroll_accounting.router,'/companies/1/payroll-calculations/1/accounting-journal'),
    (payroll_disbursements.router,'/companies/1/payroll-disbursements/1/accounting-journal'),
])
async def test_create_permission_does_not_authorize_posting(router,path):
    api=FastAPI(); api.include_router(router)
    db=AsyncMock()
    class Result:
        def __init__(self,value): self.value=value
        def scalar_one_or_none(self): return self.value
    db.execute.side_effect=[Result(1),Result(None)]
    api.dependency_overrides[get_current_user]=lambda: SimpleNamespace(id=1)
    async def session(): yield db
    api.dependency_overrides[get_db]=session
    async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
        response=await client.post(path)
    assert response.status_code==403, response.text
    db.commit.assert_not_awaited()
