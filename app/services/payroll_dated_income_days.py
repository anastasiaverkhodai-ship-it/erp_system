"""Recover dated earnings from their authoritative frozen source, not a guess."""
from datetime import timedelta
from decimal import Decimal as D, ROUND_HALF_UP
from sqlalchemy import select


def allocate_by_weights(amount, weights):
    total=sum(weights.values())
    if not total:
        if amount:raise ValueError('Positive income has no verified earned time')
        return {day:D(0) for day in weights}
    cumulative=D(0);previous=D(0);result={}
    for day,weight in sorted(weights.items()):
        cumulative+=D(weight)
        rounded=(amount*cumulative/total).quantize(D('.01'),rounding=ROUND_HALF_UP)
        result[day]=rounded-previous;previous=rounded
    return result


async def income_days(db, *, company_id, calculation, line, period):
    from app.services.payroll_statutory_service import PayrollStatutoryValidationError as Error
    from app.models.leave_request import LeaveRequest
    kind=line.line_type
    if kind in {'vacation_pay','sick_pay'}:
        if kind=='vacation_pay':
            from app.models.payroll_vacation import PayrollVacationCalculation
            from app.services.payroll_leave_correction_sources import calculation_leave_snapshot,vacation_snapshot,vacation_values
            source=await db.scalar(select(PayrollVacationCalculation).where(PayrollVacationCalculation.company_id==company_id,
                PayrollVacationCalculation.id==line.source_vacation_calculation_id))
        else:
            from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
            source=await db.scalar(select(PayrollSickLeaveCalculation).where(PayrollSickLeaveCalculation.company_id==company_id,
                PayrollSickLeaveCalculation.id==line.source_sick_leave_calculation_id))
        if source is None or source.employment_contract_id!=calculation.employment_contract_id:
            raise Error('Dated absence source ownership mismatch')
        leave=await db.get(LeaveRequest,source.leave_request_id)
        if kind=='vacation_pay':
            snapshot=await calculation_leave_snapshot(db,company_id=company_id,calculation_id=calculation.id,
                target_kind='vacation',target_id=source.id) or vacation_snapshot(source,leave)
            from datetime import date
            days={date.fromisoformat(value):vacation_values(snapshot,date.fromisoformat(value),date.fromisoformat(value))['amount']
                  for value in snapshot['used_dates'] if period.start_date.isoformat()<=value<=period.end_date.isoformat()}
        else:
            from app.services.payroll_sick_allocation import sick_period_amount
            start=max(leave.start_date,period.start_date);end=min(leave.end_date,period.end_date)
            days={start+timedelta(days=i):sick_period_amount(source,leave,start+timedelta(days=i),start+timedelta(days=i))[1]
                  for i in range((end-start).days+1)}
        if sum(days.values(),D(0))!=line.amount:raise Error('Dated absence amounts differ from payroll source')
        return days
    if kind in {'leave_compensation','severance'}:
        if kind=='severance':
            from app.models.payroll_termination_severance import PayrollTerminationSeverance
            source=await db.get(PayrollTerminationSeverance,line.source_termination_severance_id)
            day=source.termination_date if source else None
        else:
            from app.models.payroll_leave_compensation import PayrollLeaveCompensation,PayrollLeaveCompensationLine
            if line.source_leave_compensation_line_id:
                part=await db.get(PayrollLeaveCompensationLine,line.source_leave_compensation_line_id)
            else:
                from app.models.payroll_leave_correction import PayrollLeaveCompensationExtraSource
                part=await db.get(PayrollLeaveCompensationExtraSource,line.source_leave_compensation_extra_id)
            source=await db.get(PayrollLeaveCompensation,part.compensation_id) if part else None
            day=source.compensation_date if source else None
        if source is None or source.company_id!=company_id or source.employment_contract_id!=calculation.employment_contract_id:
            raise Error('Dated settlement source ownership mismatch')
        return {day:line.amount}
    if kind=='supplement':
        from app.models.payroll_supplement import PayrollSupplement
        source=await db.get(PayrollSupplement,line.source_supplement_id)
        if source is None or source.company_id!=company_id or source.payroll_input_id!=calculation.payroll_input_id:
            raise Error('Dated supplement source mismatch')
        if source.work_date is not None:return {source.work_date:line.amount}
        # A monthly bonus uses independently documented source dates; an opaque
        # adjustment cannot silently be treated as a bonus for the whole month.
        raise Error('Monthly supplement needs reviewed dated income allocation')
    if kind not in {'salary','minimum_wage'}:
        raise Error('Verified dated income allocation required for this earning type')
    from app.models.time_attendance import AttendanceRecord
    start=line.source_effective_from or period.start_date;end=line.source_effective_to or period.end_date
    attendance=list((await db.scalars(select(AttendanceRecord).where(AttendanceRecord.company_id==company_id,
        AttendanceRecord.employment_contract_id==calculation.employment_contract_id,
        AttendanceRecord.work_date>=start,AttendanceRecord.work_date<=end))).all())
    weights={row.work_date:row.worked_minutes for row in attendance}
    payload=(calculation.minimum_wage_assessment or {}).get('correction_basis',{}).get('payload',{})
    from datetime import date
    for day,minutes in payload.get('attendance_day_minutes',{}).items():
        day=date.fromisoformat(day)
        if start<=day<=end:weights[day]=minutes
    if kind=='salary':
        from app.models.payroll import PayrollInputSalarySlice
        from app.models.employee_salary_rate import EmployeeSalaryRate
        from app.services.payroll_correction_historical_salary_proof import verify_salary_rate_slice
        part=await db.scalar(select(PayrollInputSalarySlice).where(PayrollInputSalarySlice.company_id==company_id,
            PayrollInputSalarySlice.payroll_input_id==calculation.payroll_input_id,
            PayrollInputSalarySlice.salary_rate_id==line.source_salary_rate_id,
            PayrollInputSalarySlice.effective_from==start,PayrollInputSalarySlice.effective_to==end))
        rate=await db.get(EmployeeSalaryRate,line.source_salary_rate_id)
        verify_salary_rate_slice(rate,part,company_id=company_id,employment_contract_id=calculation.employment_contract_id)
        expected=payload.get('attendance_slice_minutes',{}).get(str(part.id),part.worked_minutes)
        if sum(weights.values())!=expected:raise Error('Dated attendance does not reconcile to frozen salary minutes')
    else:
        expected=payload.get('facts',{}).get('ordinary_worked_minutes')
        if expected is None or sum(weights.values())!=expected:
            raise Error('Minimum-wage dated allocation needs verified ordinary worked time')
    return allocate_by_weights(line.amount,weights)


async def verify_dated_salary_line(db, *, company_id, calculation, line):
    from app.models.payroll import PayrollInput, PayrollInputSalarySlice
    from app.models.employee_salary_rate import EmployeeSalaryRate
    from app.services.payroll_correction_historical_salary_proof import verify_salary_rate_slice
    part=await db.scalar(select(PayrollInputSalarySlice).where(PayrollInputSalarySlice.company_id==company_id,
        PayrollInputSalarySlice.payroll_input_id==calculation.payroll_input_id,
        PayrollInputSalarySlice.salary_rate_id==line.source_salary_rate_id,
        PayrollInputSalarySlice.effective_from==line.source_effective_from,
        PayrollInputSalarySlice.effective_to==line.source_effective_to))
    rate=await db.get(EmployeeSalaryRate,line.source_salary_rate_id) if line.source_salary_rate_id else None
    verify_salary_rate_slice(rate,part,company_id=company_id,employment_contract_id=calculation.employment_contract_id)
    payload=(calculation.minimum_wage_assessment or {}).get('correction_basis',{}).get('payload',{})
    minutes=payload.get('attendance_slice_minutes',{}).get(str(part.id),part.worked_minutes)
    source=await db.get(PayrollInput,calculation.payroll_input_id)
    denominator=source.monthly_norm_minutes if part.salary_rate_type=='monthly' else 60
    if not denominator:raise ValueError('Verified salary norm required for dated taxation')
    amount=(D(part.salary_rate_amount)*minutes/denominator).quantize(D('.01'),rounding=ROUND_HALF_UP)
    if amount!=line.amount:raise ValueError('Dated salary amount differs from frozen time and rate')
