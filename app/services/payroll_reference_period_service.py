"""Ordinary vacation reference months using the employee's assigned calendar."""
from calendar import monthrange
from datetime import timedelta


async def vacation_employment_month_start(db, *, company_id, contract):
    """Include a hire month only for calendar day 1 or its first working day.

    WorkSchedule is a weekly calendar. Its assignment must start on the hire
    date; a later assignment cannot establish the calendar at recruitment.
    """
    from app.services.payroll_average_history_service import PayrollAverageHistoryError
    from app.services.payroll_input_service import (
        _load_schedule_assignments, _load_schedule_days, _assignment_for_day,
        _scheduled_minutes_for_day,
    )
    hire = contract.start_date
    if hire.day == 1:
        return hire
    assignments = await _load_schedule_assignments(db, company_id=company_id,
        employment_contract_id=contract.id, date_from=hire, date_to=hire)
    assignment = _assignment_for_day(assignments, hire)
    if assignment is None:
        raise PayrollAverageHistoryError('Partial employment month requires the work schedule at hiring')
    days = await _load_schedule_days(db, company_id=company_id,
        schedule_ids={assignment.work_schedule_id})
    first = hire.replace(day=1)
    cursor = first
    while cursor <= hire:
        row = days.get((assignment.work_schedule_id, cursor.isoweekday()))
        if row and _scheduled_minutes_for_day(row) > 0:
            if cursor == hire:
                return first
            break
        cursor += timedelta(days=1)
    return hire.replace(day=monthrange(hire.year, hire.month)[1]) + timedelta(days=1)
