"""Determine the ordinary primary-employment USC floor for a partial month.

A complete calendar month qualifies directly. For a partial calendar month the
schedule and attendance must prove that every scheduled working day was worked.
The first/last assigned schedule supplies the month norm outside employment,
as in payroll_input_service._derive_monthly_norm; no Mon-Fri assumption is made.
"""
from datetime import timedelta


async def qualifies_for_monthly_usc_floor(db, *, company_id, period, contract, calculation=None):
    if (contract.start_date <= period.start_date and
            (contract.end_date is None or contract.end_date >= period.end_date)):
        return True
    from app.services.payroll_input_service import (
        _load_schedule_assignments, _load_schedule_days, _load_attendance,
        _assignment_for_day, _scheduled_minutes_for_day,
    )
    from app.services.payroll_statutory_service import PayrollStatutoryValidationError
    assignments = sorted(await _load_schedule_assignments(db, company_id=company_id,
        employment_contract_id=contract.id, date_from=period.start_date,
        date_to=period.end_date), key=lambda row: row.effective_from)
    if not assignments:
        raise PayrollStatutoryValidationError(
            'Partial-month USC eligibility requires a verified work schedule')
    days = await _load_schedule_days(db, company_id=company_id,
        schedule_ids={row.work_schedule_id for row in assignments})
    attendance = await _load_attendance(db, company_id=company_id,
        employment_contract_id=contract.id, date_from=period.start_date,
        date_to=period.end_date)
    # Use the selected immutable revision, never a later correction's view.
    corrected_minutes = {}
    if calculation is not None:
        if (calculation.company_id != company_id or
                calculation.employment_contract_id != contract.id or
                calculation.payroll_period_id != period.id):
            raise PayrollStatutoryValidationError('USC attendance revision ownership mismatch')
        corrected_minutes = ((calculation.minimum_wage_assessment or {})
            .get('correction_basis', {}).get('payload', {}).get('attendance_day_minutes', {}))
    current = period.start_date
    working_days = 0
    while current <= period.end_date:
        assignment = _assignment_for_day(assignments, current)
        employed = current >= contract.start_date and (
            contract.end_date is None or current <= contract.end_date)
        if assignment is None:
            if current < contract.start_date:
                assignment = assignments[0]
            elif contract.end_date and current > contract.end_date:
                assignment = assignments[-1]
            else:
                raise PayrollStatutoryValidationError('Gap in USC employment schedule')
        schedule_day = days.get((assignment.work_schedule_id, current.isoweekday()))
        minutes = _scheduled_minutes_for_day(schedule_day) if schedule_day else 0
        if minutes > 0:
            working_days += 1
            record = attendance.get(current)
            worked = corrected_minutes.get(current.isoformat(), record.worked_minutes if record else 0)
            if not employed or worked < minutes:
                return False
        current += timedelta(days=1)
    return working_days > 0
