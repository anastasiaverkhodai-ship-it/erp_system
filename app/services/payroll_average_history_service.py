"""Derive average-pay inputs from complete, attributed imported months.

The ordinary full-month reference window is derived from employment. This
service validates optional supplied dates; it does not infer statutory exceptions,
replacement salary, or the treatment of non-salary ERP earnings.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from app.models.employment_contract import EmploymentContract
from app.models.leave_request import LeaveRequest, LeaveRequestStatus, LeaveType
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_opening import PayrollEarningsHistory
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_vacation_service import (
    calculate_vacation_pay, get_vacation_calculation_for_leave,
    PayrollVacationConflictError,
)


class PayrollAverageHistoryError(ValueError):
    pass


class PayrollAverageHistoryNotFoundError(PayrollAverageHistoryError):
    pass


RULE_CODE = 'UA_VACATION_VERIFIED_HISTORY'
RULE_VERSION = '1-calendar-4928-IX'
# Full days covered by Law 2136-IX and the verified extension 4928-IX.
# Do not assume later extensions. Historical eligible days come from imports.
CALENDAR_RULE_START = date(2022, 3, 24)
CALENDAR_RULE_END = date(2026, 10, 30)


def next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


async def derive_leave_average_history(db, *, company_id, leave_request_id,
                                      reference_period_start=None, reference_period_end=None):
    leave = await db.scalar(select(LeaveRequest).where(
        LeaveRequest.company_id == company_id, LeaveRequest.id == leave_request_id))
    if leave is None:
        raise PayrollAverageHistoryNotFoundError('Leave request not found')
    if leave.status != LeaveRequestStatus.APPROVED:
        raise PayrollAverageHistoryError('Average pay requires approved leave')
    if leave.leave_type not in (LeaveType.ANNUAL, LeaveType.SICK):
        raise PayrollAverageHistoryError('Average pay supports annual or sick leave')
    contract = await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id == company_id,
        EmploymentContract.id == leave.employment_contract_id))
    if contract is None or contract.status == 'cancelled':
        raise PayrollAverageHistoryError('Active employment is required')
    if leave.start_date < contract.start_date or (contract.end_date and leave.end_date > contract.end_date):
        raise PayrollAverageHistoryError('Leave falls outside employment')
    if (reference_period_start is None) != (reference_period_end is None):
        raise PayrollAverageHistoryError('Supply both reference dates or neither')
    last = leave.start_date.replace(day=1) - timedelta(days=1)
    earliest = date(leave.start_date.year - 1, leave.start_date.month, 1)
    start = reference_period_start or max(earliest, contract.start_date)
    end = reference_period_end or last
    if start.day != 1 or end != last or start < earliest or start > end:
        raise PayrollAverageHistoryError('Select complete months within the twelve months preceding leave')
    if contract.start_date > earliest and contract.start_date.day != 1:
        raise PayrollAverageHistoryError('Partial employment month requires a verified reference-period decision')
    if start != max(earliest, contract.start_date):
        raise PayrollAverageHistoryError('Reference window must include all applicable completed months')
    from app.services.payroll_revision_service import current_calculation_filter
    calculations = (await db.execute(select(PayrollCalculation, PayrollPeriod).join(PayrollPeriod,
        (PayrollPeriod.id == PayrollCalculation.payroll_period_id) &
        (PayrollPeriod.company_id == PayrollCalculation.company_id)).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.employment_contract_id == contract.id,
            PayrollPeriod.start_date <= end, PayrollPeriod.end_date >= start,
            current_calculation_filter()))).all()
    histories = list((await db.scalars(select(PayrollEarningsHistory).where(
        PayrollEarningsHistory.company_id == company_id,
        PayrollEarningsHistory.employment_contract_id == contract.id,
        PayrollEarningsHistory.month >= start, PayrollEarningsHistory.month <= end,
    ).order_by(PayrollEarningsHistory.month))).all())
    months = []
    cursor = start
    while cursor <= end:
        months.append(cursor)
        cursor = next_month(cursor)
    if len({row.cutover_date for row in histories}) > 1:
        raise PayrollAverageHistoryError('Historical months have inconsistent cutover dates')
    purpose = 'vacation' if leave.leave_type == LeaveType.ANNUAL else 'sick'
    imported = {row.month: row for row in histories}
    sources = []
    for month in months:
        month_end = month.replace(day=monthrange(month.year, month.month)[1])
        matches = [(c, p) for c, p in calculations if p.start_date <= month_end and p.end_date >= month]
        row = imported.get(month)
        if row is not None and matches:
            raise PayrollAverageHistoryError('Historical month duplicates ERP earnings: ' + month.isoformat())
        if row is None:
            if not matches:
                raise PayrollAverageHistoryError('Incomplete earnings history: ' + month.isoformat())
            if len(matches) != 1 or purpose != 'vacation':
                raise PayrollAverageHistoryError('ERP earnings require an unambiguous supported classification')
            calculation, period = matches[0]
            if histories and month < histories[0].cutover_date:
                raise PayrollAverageHistoryError('ERP earnings precede the historical cutover date')
            sources.append(await _erp_vacation_source(db, company_id=company_id,
                contract_id=contract.id, calculation=calculation, period=period,
                month=month, month_end=month_end))
            continue
        earnings = Decimal(getattr(row, purpose + '_earnings'))
        days = Decimal(getattr(row, purpose + '_days'))
        if month_end >= row.cutover_date or not row.source_reference or not row.source_reference.strip():
            raise PayrollAverageHistoryError('Historical source requires a completed month and supporting reference')
        if not earnings.is_finite() or earnings < 0 or not days.is_finite() or not 0 <= days <= month_end.day:
            raise PayrollAverageHistoryError('Invalid historical earnings or eligible days')
        if earnings > 0 and days == 0:
            raise PayrollAverageHistoryError('Historical earnings require eligible days')
        sources.append(dict(source_type='earnings_history', source_id=row.id,
            source_period_start=row.month, source_period_end=month_end,
            earnings_amount=earnings, eligible_days=days, source_reference=row.source_reference))
    earnings = sum((s['earnings_amount'] for s in sources), Decimal(0))
    days = sum((s['eligible_days'] for s in sources), Decimal(0))
    if days <= 0 or earnings <= 0:
        raise PayrollAverageHistoryError('No positive historical basis; a statutory replacement-salary calculation is required')
    return dict(company_id=company_id, leave_request_id=leave.id,
        employment_contract_id=contract.id, purpose=purpose,
        reference_period_start=start, reference_period_end=end,
        eligible_earnings=earnings, eligible_days=days, currency_code='UAH',
        average_daily_amount=(earnings / days).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP),
        sources=sources)


@serialized_payroll_mutation(PayrollAverageHistoryError)
async def calculate_vacation_from_history(db, *, company_id, leave_request_id,
                                         reference_period_start=None, reference_period_end=None, calculated_by):
    if (reference_period_start is None) != (reference_period_end is None):
        raise PayrollAverageHistoryError('Supply both reference dates or neither')
    existing = await get_vacation_calculation_for_leave(db, company_id=company_id,
        leave_request_id=leave_request_id)
    if existing is not None:
        if (existing.rule_code, existing.rule_version, existing.reference_period_start,
            existing.reference_period_end) != (RULE_CODE, RULE_VERSION, reference_period_start or existing.reference_period_start,
                                               reference_period_end or existing.reference_period_end):
            raise PayrollVacationConflictError('Existing vacation calculation has different request data; use a correction')
        return existing
    basis = await derive_leave_average_history(db, company_id=company_id,
        leave_request_id=leave_request_id, reference_period_start=reference_period_start,
        reference_period_end=reference_period_end)
    leave = await db.scalar(select(LeaveRequest).where(
        LeaveRequest.company_id == company_id, LeaveRequest.id == leave_request_id))
    if not CALENDAR_RULE_START <= leave.start_date <= leave.end_date <= CALENDAR_RULE_END:
        raise PayrollAverageHistoryError('Leave calendar requires a verified rule for these dates')
    if basis['purpose'] != 'vacation':
        raise PayrollAverageHistoryError('Vacation calculation requires annual leave')
    return await calculate_vacation_pay(db, company_id=company_id, leave_request_id=leave_request_id,
        reference_period_start=basis['reference_period_start'], reference_period_end=basis['reference_period_end'],
        eligible_earnings=basis['eligible_earnings'], eligible_days=basis['eligible_days'],
        rule_code=RULE_CODE, rule_version=RULE_VERSION, calculated_by=calculated_by,
        sources=basis['sources'])


async def _erp_vacation_source(db, *, company_id, contract_id, calculation, period, month, month_end):
    """Ordinary salary months only; do not guess bonus or absence treatment."""
    if period.status != 'finalized' or period.start_date != month or period.end_date != month_end:
        raise PayrollAverageHistoryError('ERP source requires a finalized complete payroll month')
    if not CALENDAR_RULE_START <= month <= month_end <= CALENDAR_RULE_END:
        raise PayrollAverageHistoryError('ERP reference calendar requires a verified dated rule')
    if calculation.currency_code != 'UAH':
        raise PayrollAverageHistoryError('ERP source must be denominated in UAH')
    lines = list((await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == calculation.id))).all())
    if not lines or any(line.line_type != 'salary' for line in lines):
        raise PayrollAverageHistoryError('ERP non-salary earnings require an explicit average-pay classification')
    earnings = sum((Decimal(line.amount) for line in lines), Decimal(0))
    if earnings != calculation.gross_amount or any(line.currency_code != 'UAH' for line in lines):
        raise PayrollAverageHistoryError('ERP source lines do not reconcile to gross earnings')
    leaves = list((await db.scalars(select(LeaveRequest).where(
        LeaveRequest.company_id == company_id, LeaveRequest.employment_contract_id == contract_id,
        LeaveRequest.status == LeaveRequestStatus.APPROVED,
        LeaveRequest.start_date <= month_end, LeaveRequest.end_date >= month))).all())
    if any(leave.leave_type != LeaveType.UNPAID for leave in leaves):
        raise PayrollAverageHistoryError('ERP paid or other absences require an explicit average-pay classification')
    from app.models.time_attendance import AttendanceRecord as Attendance
    if await db.scalar(select(Attendance.id).where(Attendance.company_id == company_id,
        Attendance.employment_contract_id == contract_id, Attendance.work_date >= month,
        Attendance.work_date <= month_end, Attendance.status == 'absent').limit(1)):
        raise PayrollAverageHistoryError('Unclassified absence in ERP reference month')
    excluded = set()
    for leave in leaves:
        day = max(month, leave.start_date)
        while day <= min(month_end, leave.end_date):
            excluded.add(day)
            day += timedelta(days=1)
    days = Decimal(month_end.day - len(excluded))
    if days == 0 and earnings:
        raise PayrollAverageHistoryError('ERP earnings have no eligible calendar days')
    return dict(source_type='payroll_calculation', source_id=calculation.id,
        source_period_start=month, source_period_end=month_end,
        earnings_amount=earnings, eligible_days=days,
        source_reference=f'ERP payroll calculation {calculation.id}, revision {calculation.revision}')
