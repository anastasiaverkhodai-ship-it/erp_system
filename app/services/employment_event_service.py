"""Append-only personnel events and historical employment terms."""
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
from sqlalchemy import select
from app.models.employment_event import EmploymentEvent
from app.models.employment_contract import EmploymentContract
from app.models.time_attendance import AttendanceRecord
from app.models.leave_request import LeaveRequest
from app.services.hr_change_service import snapshot
from app.services.payroll_mutation_guard import serialized_payroll_mutation, ensure_payroll_source_editable
from app.services.employment_structure_service import (
    EmploymentStructureLifecycleError, EmploymentStructureNotFoundError,
    get_employment_contract, update_employment_contract,
)

TERMS={'department_id','position_id','employment_kind','work_arrangement'}


async def list_employment_events(db, *, company_id, contract_id):
    await get_employment_contract(db,company_id=company_id,contract_id=contract_id)
    return list((await db.scalars(select(EmploymentEvent).where(
        EmploymentEvent.company_id==company_id,EmploymentEvent.employment_contract_id==contract_id)
        .order_by(EmploymentEvent.effective_date,EmploymentEvent.id))).all())


async def employment_state_on(db, *, company_id, contract_id, on_date):
    contract=await get_employment_contract(db,company_id=company_id,contract_id=contract_id)
    events=await list_employment_events(db,company_id=company_id,contract_id=contract_id)
    state=events[0].before_state.copy() if events else snapshot(contract)
    for event in events:
        if event.effective_date > on_date:
            break
        state=event.after_state.copy()
    reversed_ids={event.reversal_of_id for event in events if event.reversal_of_id is not None}
    active_events=[event for event in events if event.event_type!='reversal' and event.id not in reversed_ids]
    # A termination date is the final day of employment, not the first absent day.
    start=date.fromisoformat(state['start_date'])
    end=date.fromisoformat(state['end_date']) if state.get('end_date') else None
    return dict(company_id=company_id,employment_contract_id=contract_id,on_date=on_date,
        terms=state, employed=start <= on_date and (end is None or on_date <= end) and state['status']!='cancelled',
        history_verified_from=active_events[0].effective_date if active_events else None,
        history_complete=bool(active_events) and on_date >= active_events[0].effective_date)


async def contract_kind_segments(db, contract_id, company_id, start, end, kind):
    events=list((await db.scalars(select(EmploymentEvent).where(
        EmploymentEvent.company_id==company_id,EmploymentEvent.employment_contract_id==contract_id)
        .order_by(EmploymentEvent.effective_date,EmploymentEvent.id))).all()) if contract_id else []
    until=end or date.max
    current=events[0].before_state.get('employment_kind') if events else kind
    cursor=start
    result=[]
    for event in events:
        if event.effective_date > until:
            break
        if event.effective_date > cursor:
            result.append((current,cursor,event.effective_date-timedelta(days=1)))
        current=event.after_state.get('employment_kind')
        cursor=max(cursor,event.effective_date)
    if cursor <= until:
        result.append((current,cursor,until))
    return result


async def _apply_projection(db, *, company_id, contract_id, state, actor):
    data={key:state[key] for key in TERMS}
    data.update(start_date=date.fromisoformat(state['start_date']),
        end_date=date.fromisoformat(state['end_date']) if state.get('end_date') else None,
        status=state['status'])
    previous=db.info.get('employment_event_contract')
    db.info['employment_event_contract']=contract_id
    try:
        return await update_employment_contract(db,company_id=company_id,contract_id=contract_id,
            **data,fields_set=set(data),changed_by=actor)
    finally:
        if previous is None:
            db.info.pop('employment_event_contract',None)
        else:
            db.info['employment_event_contract']=previous


async def _ensure_termination_sources(db, company_id, contract_id, effective_date):
    attendance=await db.scalar(select(AttendanceRecord.id).where(
        AttendanceRecord.company_id==company_id,AttendanceRecord.employment_contract_id==contract_id,
        AttendanceRecord.work_date>effective_date).limit(1))
    leave=await db.scalar(select(LeaveRequest.id).where(LeaveRequest.company_id==company_id,
        LeaveRequest.employment_contract_id==contract_id,LeaveRequest.status=='approved',
        LeaveRequest.end_date>effective_date).limit(1))
    if attendance is not None or leave is not None:
        raise EmploymentStructureLifecycleError('Resolve attendance and approved leave after termination first')


@serialized_payroll_mutation(EmploymentStructureLifecycleError)
async def create_employment_event(db, *, company_id, contract_id, data, created_by):
    payload=data.model_dump(mode='json',exclude_unset=True)
    existing=await db.scalar(select(EmploymentEvent).where(EmploymentEvent.company_id==company_id,
        EmploymentEvent.request_key==data.request_key))
    if existing is not None:
        if existing.employment_contract_id != contract_id or existing.request_payload != payload:
            raise EmploymentStructureLifecycleError('Request key was used for different employment event data')
        return existing
    contract=await get_employment_contract(db,company_id=company_id,contract_id=contract_id,lock_row=True)
    if contract.status=='cancelled' or contract.employment_kind is None:
        raise EmploymentStructureLifecycleError('Use a non-cancelled contract with a classified employment kind')
    today=datetime.now(ZoneInfo('Europe/Kyiv')).date()
    if data.effective_date>today or data.order_date>today:
        raise EmploymentStructureLifecycleError('Future orders must remain unposted until their effective date')
    if data.effective_date<contract.start_date or (contract.end_date and data.effective_date>contract.end_date):
        raise EmploymentStructureLifecycleError('Event date must fall within employment')
    events=await list_employment_events(db,company_id=company_id,contract_id=contract_id)
    if events and data.effective_date<=events[-1].effective_date:
        raise EmploymentStructureLifecycleError('Append events in effective-date order; use a correction for earlier events')
    if data.event_type=='hire' and (events or data.effective_date!=contract.start_date):
        raise EmploymentStructureLifecycleError('Hire evidence must be the first event on the contract start date')
    if data.event_type=='termination' and contract.status=='ended':
        raise EmploymentStructureLifecycleError('Employment is already ended')
    await ensure_payroll_source_editable(db,company_id=company_id,date_from=data.effective_date,
        contract_id=contract_id,error_type=EmploymentStructureLifecycleError)
    before=snapshot(contract)
    after=before.copy()
    if data.event_type=='transfer':
        for field in TERMS & data.model_fields_set:
            value=getattr(data,field)
            after[field]=getattr(value,'value',value)
        if all(before[field]==after[field] for field in TERMS):
            raise EmploymentStructureLifecycleError('Transfer does not change employment terms')
    elif data.event_type=='termination':
        await _ensure_termination_sources(db,company_id,contract_id,data.effective_date)
        after.update(status='ended',end_date=data.effective_date.isoformat())
    event=EmploymentEvent(company_id=company_id,employment_contract_id=contract_id,
        event_type=data.event_type,effective_date=data.effective_date,order_number=data.order_number,
        order_date=data.order_date,reason=data.reason,request_key=data.request_key,
        before_state=before,after_state=after,request_payload=payload,created_by=created_by)
    # The savepoint keeps an invalid transition from leaving an event behind even
    # when a service caller catches the domain error and continues its transaction.
    async with db.begin_nested():
        db.add(event)
        await db.flush()
        await _apply_projection(db,company_id=company_id,contract_id=contract_id,state=after,actor=created_by)
    return event


@serialized_payroll_mutation(EmploymentStructureLifecycleError)
async def reverse_employment_event(db, *, company_id, contract_id, event_id, data, created_by):
    payload=dict(data.model_dump(mode='json'),reversal_of_id=event_id)
    existing=await db.scalar(select(EmploymentEvent).where(EmploymentEvent.company_id==company_id,
        EmploymentEvent.request_key==data.request_key))
    if existing is not None:
        if existing.employment_contract_id!=contract_id or existing.request_payload!=payload:
            raise EmploymentStructureLifecycleError('Request key was used for different employment event data')
        return existing
    events=await list_employment_events(db,company_id=company_id,contract_id=contract_id)
    source=next((event for event in events if event.id==event_id),None)
    if source is None:
        raise EmploymentStructureNotFoundError('Employment event not found')
    if source is not events[-1] or source.event_type=='reversal':
        raise EmploymentStructureLifecycleError('Only the latest original employment event can be reversed')
    if data.order_date>datetime.now(ZoneInfo('Europe/Kyiv')).date():
        raise EmploymentStructureLifecycleError('Future correction orders cannot be posted')
    await ensure_payroll_source_editable(db,company_id=company_id,date_from=source.effective_date,
        contract_id=contract_id,error_type=EmploymentStructureLifecycleError)
    # Undoing a hire only removes its evidence, not the pre-existing contract.
    event=EmploymentEvent(company_id=company_id,employment_contract_id=contract_id,
        event_type='reversal',effective_date=source.effective_date,order_number=data.order_number,
        order_date=data.order_date,reason=data.reason,request_key=data.request_key,
        before_state=source.after_state.copy(),after_state=source.before_state.copy(),
        request_payload=payload,reversal_of_id=source.id,created_by=created_by)
    async with db.begin_nested():
        db.add(event)
        await db.flush()
        await _apply_projection(db,company_id=company_id,contract_id=contract_id,
            state=event.after_state,actor=created_by)
    return event


@serialized_payroll_mutation(EmploymentStructureLifecycleError)
async def create_employment_event_batch(db, *, company_id, data, created_by):
    from app.services.employment_structure_service import _assert_no_contract_overlap
    result=[]
    employee_ids=set()
    async with db.begin_nested():
        db.info['employment_event_batch']=True
        try:
            for item in data.events:
                event=await create_employment_event(db,company_id=company_id,contract_id=item.contract_id,
                    data=item.event,created_by=created_by)
                result.append(event)
                employee_ids.add(event.after_state['employee_id'])
        finally:
            db.info.pop('employment_event_batch',None)
        for employee_id in employee_ids:
            await _assert_no_contract_overlap(db,company_id=company_id,employee_id=employee_id,
                start_date=date.min,end_date=None,status='cancelled')
    return result
