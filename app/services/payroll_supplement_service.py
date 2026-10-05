"""Typed gross supplements; time premiums never silently replace salary."""
from datetime import datetime, timezone, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
from sqlalchemy import select
from app.models.payroll import PayrollInput,PayrollPeriod,PayrollInputSalarySlice
from app.models.payroll_supplement import PayrollSupplement
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class PayrollSupplementError(Exception): pass
class PayrollSupplementNotFoundError(PayrollSupplementError): pass


async def _sources(db,company_id,input_id,editable=False):
    source=await db.scalar(select(PayrollInput).where(PayrollInput.company_id==company_id,
        PayrollInput.id==input_id).execution_options(populate_existing=True))
    if source is None: raise PayrollSupplementNotFoundError('Payroll input not found')
    period=await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id==company_id,
        PayrollPeriod.id==source.payroll_period_id).execution_options(populate_existing=True))
    if period is None: raise PayrollSupplementNotFoundError('Payroll period not found')
    if editable and period.status!='draft':
        raise PayrollSupplementError('Finalized payroll supplements require a correction')
    return source,period


async def list_payroll_supplements(db, *, company_id, payroll_input_id):
    await _sources(db,company_id,payroll_input_id)
    return list((await db.scalars(select(PayrollSupplement).where(PayrollSupplement.company_id==company_id,
        PayrollSupplement.payroll_input_id==payroll_input_id).order_by(PayrollSupplement.work_date,PayrollSupplement.id))).all())


def time_premium(*,kind,minutes,coefficient,rate_type,salary_amount,monthly_norm_minutes):
    if minutes<=0: raise PayrollSupplementError('Premium minutes must be positive')
    if rate_type=='hourly': hourly=Decimal(salary_amount)
    elif rate_type=='monthly' and monthly_norm_minutes and monthly_norm_minutes>0:
        hourly=Decimal(salary_amount)*Decimal(60)/Decimal(monthly_norm_minutes)
    else: raise PayrollSupplementError('Time premium requires a valid salary rate and monthly norm')
    if kind=='regular_extra': premium_rate=Decimal(0)
    elif kind in {'overtime','rest_day'}: premium_rate=hourly
    elif kind=='night' and Decimal(coefficient)>=Decimal('0.20'):
        premium_rate=hourly*Decimal(coefficient)
    else: raise PayrollSupplementError('Invalid time premium kind or coefficient')
    amount=(premium_rate*Decimal(minutes)/Decimal(60)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    return premium_rate,amount


def night_window_minutes(start, end, timezone_name):
    """Elapsed minutes inside local 22:00–06:00 windows, including DST changes."""
    if start is None or end is None:
        return 0
    zone = ZoneInfo(timezone_name)
    start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    day = start.astimezone(zone).date() - timedelta(days=1)
    last_day = end.astimezone(zone).date()
    seconds = 0
    while day <= last_day:
        lower = datetime.combine(day, time(22), zone).astimezone(timezone.utc)
        upper = datetime.combine(day + timedelta(days=1), time(6), zone).astimezone(timezone.utc)
        seconds += max(0, (min(end_utc, upper) - max(start_utc, lower)).total_seconds())
        day += timedelta(days=1)
    return int(seconds // 60)


async def derive_supplement_lines(db, *, company_id, payroll_input, period, salary_slices, strict=True):
    from app.services.payroll_input_service import (_load_schedule_assignments,_load_schedule_days,
        _load_attendance,_assignment_for_day,_scheduled_minutes_for_day)
    from app.services.employment_event_service import employment_state_on
    rows=list((await db.scalars(select(PayrollSupplement).where(PayrollSupplement.company_id==company_id,
        PayrollSupplement.payroll_input_id==payroll_input.id,PayrollSupplement.cancelled_at.is_(None))
        .order_by(PayrollSupplement.work_date,PayrollSupplement.id))).all())
    assignments=await _load_schedule_assignments(db,company_id=company_id,
        employment_contract_id=payroll_input.employment_contract_id,date_from=period.start_date,date_to=period.end_date)
    days=await _load_schedule_days(db,company_id=company_id,schedule_ids={row.work_schedule_id for row in assignments})
    attendance=await _load_attendance(db,company_id=company_id,
        employment_contract_id=payroll_input.employment_contract_id,date_from=period.start_date,date_to=period.end_date)
    from app.models.time_attendance import WorkSchedule
    schedules = {row.id: row for row in (await db.scalars(select(WorkSchedule).where(
        WorkSchedule.company_id == company_id,
        WorkSchedule.id.in_({a.work_schedule_id for a in assignments}),
    ))).all()}
    scheduled_by_day = {}
    extra_by_day={}
    for day,record in attendance.items():
        assignment=_assignment_for_day(assignments,day)
        if assignment is None: raise PayrollSupplementError('Attendance lacks a work schedule')
        planned=days.get((assignment.work_schedule_id,day.isoweekday()))
        scheduled=_scheduled_minutes_for_day(planned) if planned else 0
        scheduled_by_day[day] = scheduled
        extra_by_day[day]=max(0,record.worked_minutes-scheduled)
    extra_used={};night_used={};slice_coverage={};lines=[]
    for row in rows:
        matching=[part for part in salary_slices if part.effective_from<=row.work_date<=part.effective_to]
        if len(matching)!=1: raise PayrollSupplementError('Supplement lacks exactly one salary-rate snapshot')
        part=matching[0]
        rate_type=str(getattr(part.salary_rate_type,'value',part.salary_rate_type))
        if row.kind in {'bonus','holiday_bonus','hardship'}:
            premium_rate,amount=Decimal(row.amount),Decimal(row.amount)
            quantity=Decimal(1)
        else:
            worked=attendance.get(row.work_date)
            if worked is None or worked.worked_minutes<=0:
                raise PayrollSupplementError('Time supplement requires worked attendance')
            if row.kind=='night':
                night_used[row.work_date]=night_used.get(row.work_date,0)+row.minutes
                assignment = _assignment_for_day(assignments, row.work_date)
                window = night_window_minutes(worked.actual_start_at, worked.actual_end_at,
                    schedules[assignment.work_schedule_id].timezone)
                if night_used[row.work_date] > min(worked.worked_minutes, window):
                    raise PayrollSupplementError('Night minutes exceed actual attendance in the local night window')
            else:
                if row.kind == 'rest_day' and scheduled_by_day[row.work_date] != 0:
                    raise PayrollSupplementError('Rest-day supplement requires a scheduled non-working day')
                extra_used[row.work_date]=extra_used.get(row.work_date,0)+row.minutes
                if extra_used[row.work_date]>extra_by_day.get(row.work_date,0):
                    raise PayrollSupplementError('Extra minutes exceed time outside the assigned schedule')
                if row.kind=='regular_extra':
                    state=await employment_state_on(db,company_id=company_id,
                        contract_id=payroll_input.employment_contract_id,on_date=row.work_date)
                    if state['terms']['work_arrangement']!='part_time' or worked.worked_minutes>480:
                        raise PayrollSupplementError('Regular extra time requires part-time employment within the normal daily limit')
                slice_coverage[part.id]=slice_coverage.get(part.id,0)+row.minutes
            premium_rate,amount=time_premium(kind=row.kind,minutes=row.minutes,coefficient=row.coefficient,
                rate_type=rate_type,salary_amount=part.salary_rate_amount,
                monthly_norm_minutes=payroll_input.monthly_norm_minutes)
            quantity=(Decimal(row.minutes)/60).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP)
        lines.append(dict(line_type='supplement',description=f'{row.kind}: {row.source_reference}',
            quantity=quantity,rate=premium_rate.quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP),
            amount=amount,currency_code=part.currency_code,source_supplement_id=row.id,
            salary_rate_type=rate_type,source_salary_rate_id=part.salary_rate_id,
            source_effective_from=row.work_date,source_effective_to=row.work_date))
    if strict and any(minutes!=extra_used.get(day,0) for day,minutes in extra_by_day.items()):
        raise PayrollSupplementError('Classify all time outside the schedule before finalizing payroll')
    allowed={part.id for part in salary_slices if max(0,part.worked_minutes-part.scheduled_minutes)<=slice_coverage.get(part.id,0)}
    return lines,allowed


@serialized_payroll_mutation(PayrollSupplementError)
async def create_payroll_supplement(db, *, company_id,payroll_input_id,data,created_by):
    fingerprint=generate_request_fingerprint(dict(payroll_input_id=payroll_input_id,**data.model_dump(mode='json',exclude={'request_key'})))
    existing=await db.scalar(select(PayrollSupplement).where(PayrollSupplement.company_id==company_id,
        PayrollSupplement.request_key==data.request_key))
    if existing:
        if existing.request_fingerprint!=fingerprint or existing.cancelled_at is not None:
            raise PayrollSupplementError('Request key belongs to different or cancelled supplement')
        return existing
    source,period=await _sources(db,company_id,payroll_input_id,editable=True)
    if not period.start_date<=data.work_date<=period.end_date:
        raise PayrollSupplementError('Supplement date must be in its payroll period')
    from app.services.payroll_input_service import refresh_payroll_input
    source=await refresh_payroll_input(db,company_id=company_id,payroll_input_id=source.id)
    slices=list((await db.scalars(select(PayrollInputSalarySlice).where(PayrollInputSalarySlice.company_id==company_id,
        PayrollInputSalarySlice.payroll_input_id==source.id))).all())
    row=PayrollSupplement(company_id=company_id,payroll_input_id=source.id,
        **data.model_dump(),request_fingerprint=fingerprint,created_by=created_by)
    async with db.begin_nested():
        db.add(row);await db.flush()
        await derive_supplement_lines(db,company_id=company_id,payroll_input=source,period=period,salary_slices=slices,strict=False)
    return row


@serialized_payroll_mutation(PayrollSupplementError)
async def cancel_payroll_supplement(db, *, company_id,supplement_id,cancelled_by):
    row=await db.scalar(select(PayrollSupplement).where(PayrollSupplement.company_id==company_id,
        PayrollSupplement.id==supplement_id).execution_options(populate_existing=True))
    if row is None: raise PayrollSupplementNotFoundError('Payroll supplement not found')
    if row.cancelled_at is not None: return row
    await _sources(db,company_id,row.payroll_input_id,editable=True)
    row.cancelled_at=datetime.now(timezone.utc);row.cancelled_by=cancelled_by
    await db.flush();return row
