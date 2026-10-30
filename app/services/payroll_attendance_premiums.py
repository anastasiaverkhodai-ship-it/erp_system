"""Rebuild time premiums from reviewed intervals and historical salary terms."""
from decimal import Decimal as D, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models.payroll_supplement import PayrollSupplement
from app.models.time_attendance import WorkSchedule
from app.services.payroll_supplement_service import time_premium,night_window_minutes

TIME_KINDS={'night','overtime','rest_day','regular_extra'}

async def prepare_time_premiums(db, *, company_id, calculation, payroll_input, period, slices, lines, changes, days):
    from app.services.payroll_input_service import (_load_schedule_assignments,_load_schedule_days,
        _assignment_for_day,_scheduled_minutes_for_day)
    ids=[line.source_supplement_id for line in lines if line.source_supplement_id]
    sources={row.id:row for row in (await db.scalars(select(PayrollSupplement).where(
        PayrollSupplement.company_id==company_id,PayrollSupplement.id.in_(ids)))).all()}
    by_date={day.work_date:day for day in days}
    required=any(day.premiums is not None for day in days) or any(row.kind in TIME_KINDS for row in sources.values())
    if not required:return
    assignments=await _load_schedule_assignments(db,company_id=company_id,
        employment_contract_id=calculation.employment_contract_id,date_from=period.start_date,date_to=period.end_date)
    schedule_days=await _load_schedule_days(db,company_id=company_id,schedule_ids={a.work_schedule_id for a in assignments})
    for change in changes:
        day=by_date[change['work_date']]
        old=[row for row in sources.values() if row.kind in TIME_KINDS and row.work_date==day.work_date]
        part=next((part for part in slices if part.effective_from<=day.work_date<=part.effective_to),None)
        if part is None:raise ValueError('Corrected work requires a historical salary slice')
        change['old_slice_extra']=sum(row.minutes for row in sources.values() if row.kind in TIME_KINDS-{'night'}
            and part.effective_from<=row.work_date<=part.effective_to)
        change['old_slice_overtime']=sum(row.minutes for row in sources.values() if row.kind=='overtime'
            and part.effective_from<=row.work_date<=part.effective_to)
        if day.premiums is None:
            if old:raise ValueError('Supply all corrected time premiums, or an empty list to remove them')
            continue
        part=next((part for part in slices if part.effective_from<=day.work_date<=part.effective_to),None)
        assignment=_assignment_for_day(assignments,day.work_date)
        if part is None or assignment is None:raise ValueError('Corrected premiums require historical rate and assigned schedule')
        planned=schedule_days.get((assignment.work_schedule_id,day.work_date.isoweekday()))
        scheduled=_scheduled_minutes_for_day(planned) if planned else 0
        schedule=await db.get(WorkSchedule,assignment.work_schedule_id)
        if day.actual_start_at and day.actual_start_at.astimezone(ZoneInfo(schedule.timezone)).date()!=day.work_date:
            raise ValueError('Corrected interval starts on a different local work date')
        extra=sum(p.minutes for p in day.premiums if p.kind!='night')
        if extra!=max(0,day.worked_minutes-scheduled):
            raise ValueError('Classify all corrected minutes outside the assigned schedule exactly once')
        prepared=[]
        for premium in day.premiums:
            if premium.kind=='night':
                if not day.actual_start_at or premium.minutes>min(day.worked_minutes,night_window_minutes(
                        day.actual_start_at,day.actual_end_at,schedule.timezone)):
                    raise ValueError('Corrected night minutes exceed documented night attendance')
            if premium.kind=='rest_day' and scheduled:
                raise ValueError('Rest-day premium requires a scheduled non-working day')
            if premium.kind=='regular_extra':
                from app.services.employment_event_service import employment_state_on
                state=await employment_state_on(db,company_id=company_id,contract_id=calculation.employment_contract_id,on_date=day.work_date)
                if state['terms']['work_arrangement']!='part_time' or day.worked_minutes>480:
                    raise ValueError('Regular extra work requires part-time employment within the ordinary daily limit')
            rate,amount=time_premium(kind=premium.kind,minutes=premium.minutes,coefficient=premium.coefficient,
                rate_type=part.salary_rate_type,salary_amount=part.salary_rate_amount,monthly_norm_minutes=payroll_input.monthly_norm_minutes)
            prepared.append(dict(kind=premium.kind,minutes=premium.minutes,coefficient=str(premium.coefficient),
                amount=str(amount),rate=str(rate.quantize(D('.0001'),rounding=ROUND_HALF_UP)),source_id=None))
        change['premium_replacement']=dict(old=[dict(source_id=row.id,kind=row.kind,minutes=row.minutes,
            amount=str(next(line.amount for line in lines if line.source_supplement_id==row.id))) for row in old],new=prepared,
            scheduled_minutes=scheduled,
            # Overtime already present on other days is part of the frozen monthly guarantee.
            old_slice_overtime=sum(row.minutes for row in sources.values() if row.kind=='overtime'
                and part.effective_from<=row.work_date<=part.effective_to))


async def materialize_time_premiums(db, *, company_id, payroll_input_id, request, changes, actor_id):
    for change in changes:
        for index,premium in enumerate(change.get('premium_replacement',{}).get('new',[])):
            row=PayrollSupplement(company_id=company_id,payroll_input_id=payroll_input_id,
                correction_request_id=request.id,kind=premium['kind'],work_date=change['work_date'],
                minutes=premium['minutes'],coefficient=D(premium['coefficient']),amount=D(premium['amount']),
                source_reference=request.payload['document_reference'],request_key=f'attendance:{request.id}:{change["work_date"]}:{index}',
                request_fingerprint=request.fingerprint,created_by=actor_id)
            db.add(row);await db.flush();premium['source_id']=row.id
