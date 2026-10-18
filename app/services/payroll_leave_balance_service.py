"""Serialized, dated leave-day ledger. Financial compensation is a separate source."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from app.models.employment_contract import EmploymentContract
from app.models.payroll_opening import PayrollLeaveOpening
from app.models.payroll_leave_balance import PayrollLeaveAccount as Account, PayrollLeaveBalanceEntry as Entry, PayrollLeaveAccrual as Accrual
from app.services.idempotency_fingerprint_service import generate_request_fingerprint
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_leave_entitlement_math import EntitlementSegment, annual_entitlement


class LeaveBalanceError(ValueError):
    pass


async def require_account(db, company_id, account_id):
    row=await db.scalar(select(Account).where(Account.company_id==company_id,Account.id==account_id))
    if row is None:
        raise LeaveBalanceError('Leave account not found in company')
    return row


def _entry(*, company_id, account_id, kind, effective_date, bucket, days, reference,
           request_key, fingerprint, line_no, actor_id, **sources):
    return Entry(company_id=company_id,account_id=account_id,kind=kind,
        effective_date=effective_date,acquisition_bucket=bucket,days=days,
        source_reference=reference,request_key=request_key,request_fingerprint=fingerprint,
        line_no=line_no,created_by=actor_id,**sources)


@serialized_payroll_mutation(LeaveBalanceError)
async def create_leave_account(db, *, company_id, data, actor_id):
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    old=await db.scalar(select(Account).where(Account.company_id==company_id,Account.request_key==data.request_key))
    if old is not None:
        if old.request_fingerprint!=fingerprint:
            raise LeaveBalanceError('Request key contains different entitlement data')
        return old
    contract=await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id==company_id,EmploymentContract.id==data.employment_contract_id))
    if contract is None or contract.status=='cancelled':
        raise LeaveBalanceError('Employment contract not found or cancelled')
    if data.period_end < contract.start_date:
        raise LeaveBalanceError('Entitlement precedes employment')
    if data.leave_kind != 'social':
        from calendar import monthrange
        anniversary = date(data.period_start.year, contract.start_date.month,
            min(contract.start_date.day, monthrange(data.period_start.year, contract.start_date.month)[1]))
        next_anniversary = date(data.period_start.year+1, contract.start_date.month,
            min(contract.start_date.day, monthrange(data.period_start.year+1, contract.start_date.month)[1]))
        if data.period_start != anniversary or data.period_end != next_anniversary-timedelta(days=1):
            raise LeaveBalanceError('Annual entitlement must use the employment anniversary working year')

    overlap=select(Account.id).where(Account.company_id==company_id,
        Account.employment_contract_id==contract.id,Account.leave_kind==data.leave_kind,
        Account.period_start<=data.period_end,Account.period_end>=data.period_start)
    if data.leave_kind=='additional':
        overlap=overlap.where(Account.entitlement_code==data.entitlement_code)
    if await db.scalar(overlap.limit(1)):
        raise LeaveBalanceError('Overlapping entitlement account would duplicate leave rights')
    if data.leave_kind=='annual' and any(segment.annual_days<24 for segment in data.segments):
        raise LeaveBalanceError('Basic annual allowance cannot be below 24 days')
    if data.leave_kind!='social':
        # Validate complete coverage even before the first accrual.
        try:
            annual_entitlement(year_start=data.period_start,year_end=data.period_end,
                through=data.period_end,segments=[EntitlementSegment(s.start,s.end,s.annual_days) for s in data.segments],
                excluded_dates=data.excluded_service_dates,calendar_exclusions=data.calendar_exclusions)
        except ValueError as exc:
            raise LeaveBalanceError(str(exc)) from exc
    opening=None
    start=max(data.period_start,contract.start_date)
    old_days=new_days=Decimal(0)
    if data.opening_id is not None:
        opening=await db.scalar(select(PayrollLeaveOpening).where(
            PayrollLeaveOpening.company_id==company_id,PayrollLeaveOpening.id==data.opening_id))
        if opening is None or (opening.employment_contract_id,opening.leave_type,opening.working_year_start,opening.working_year_end)!=(contract.id,data.leave_kind,data.period_start,data.period_end):
            raise LeaveBalanceError('Opening balance does not match entitlement')
        if await db.scalar(select(Account.id).where(Account.company_id==company_id,Account.opening_id==opening.id)):
            raise LeaveBalanceError('Opening balance already assigned')
        if data.period_end<=date(2023,12,31):
            old_days=opening.remaining_days
        elif data.period_start>=date(2024,1,1):
            new_days=opening.remaining_days
        else:
            if data.opening_before_2024 is None:
                raise LeaveBalanceError('Cross-2023 opening requires documented acquisition split')
            old_days=data.opening_before_2024
            new_days=opening.remaining_days-old_days
        if old_days<0 or new_days<0:
            raise LeaveBalanceError('Opening acquisition split exceeds remaining days')
        if data.opening_before_2024 is not None and data.opening_before_2024!=old_days:
            raise LeaveBalanceError('Opening acquisition split is inconsistent')
        start=min(data.period_end+timedelta(days=1),max(start,opening.as_of+timedelta(days=1)))
    elif data.opening_before_2024 is not None:
        raise LeaveBalanceError('Acquisition split requires an opening source')
    if start>data.period_end+timedelta(days=1):
        raise LeaveBalanceError('No employment in entitlement year')
    account=Account(company_id=company_id,employment_contract_id=contract.id,leave_kind=data.leave_kind,
        entitlement_code=data.entitlement_code,period_start=data.period_start,period_end=data.period_end,
        accrual_start=start,opening_id=data.opening_id,basis=data.model_dump(mode='json'),
        source_reference=data.source_reference,request_key=data.request_key,request_fingerprint=fingerprint,created_by=actor_id)
    async with db.begin_nested():
        db.add(account);await db.flush()
        for bucket,amount in (('before_2024',old_days),('from_2024',new_days)):
            if amount:
                db.add(_entry(company_id=company_id,account_id=account.id,kind='opening',
                    effective_date=opening.as_of,bucket=bucket,days=amount,reference=opening.source_reference,
                    request_key='opening:'+str(account.id),fingerprint=fingerprint,
                    line_no=1 if bucket=='before_2024' else 2,actor_id=actor_id))
        await db.flush()
    return account


@serialized_payroll_mutation(LeaveBalanceError)
async def accrue_leave_days(db, *, company_id, account_id, data, actor_id):
    account=await require_account(db,company_id,account_id)
    fingerprint=generate_request_fingerprint(dict(account_id=account_id,**data.model_dump(mode='json')))
    document=await db.scalar(select(Accrual).where(Accrual.company_id==company_id,Accrual.request_key==data.request_key))
    if document is not None:
        if document.request_fingerprint!=fingerprint:
            raise LeaveBalanceError('Request key contains different accrual data')
        return list((await db.scalars(select(Entry).where(Entry.company_id==company_id,Entry.request_key=='accrual:'+str(document.id)))).all())
    if not data.source_reference.strip() or not data.request_key.strip():
        raise LeaveBalanceError('Accrual needs supporting evidence')
    contract=await db.get(EmploymentContract,account.employment_contract_id)
    through=min(data.through,account.period_end,contract.end_date or account.period_end)
    if through<account.accrual_start:
        raise LeaveBalanceError('No unimported entitlement period at requested date')
    latest=await db.scalar(select(func.max(Entry.effective_date)).where(Entry.company_id==company_id,
        Entry.account_id==account.id))
    latest_document=await db.scalar(select(func.max(Accrual.through)).where(Accrual.company_id==company_id,Accrual.account_id==account.id))
    latest=max(filter(None,(latest,latest_document)),default=None)
    if latest and data.through<latest:
        raise LeaveBalanceError('Cannot accrue behind recorded leave ledger events')
    basis=account.basis
    if account.leave_kind=='social':
        if account.opening_id is not None:
            raise LeaveBalanceError('Social year already represented by its imported remainder')
        amounts={'before_2024':Decimal(0),'from_2024':Decimal(0)}
        amounts['before_2024' if account.period_end.year<=2023 else 'from_2024']=Decimal(basis['social_days'])
    else:
        exclusions={date.fromisoformat(d) for d in basis['excluded_service_dates']}
        cursor=account.period_start
        while cursor<account.accrual_start:
            exclusions.add(cursor);cursor+=timedelta(days=1)
        calendar_exclusions={date.fromisoformat(d) for d in basis['calendar_exclusions']}
        if account.leave_kind=='additional':
            planned={date.fromisoformat(d) for d in basis.get('planned_work_dates', [])}
            if not planned:
                raise LeaveBalanceError('Additional leave requires a documented full-year working calendar')
            cursor=account.period_start
            while cursor<=account.period_end:
                if cursor not in planned:
                    calendar_exclusions.add(cursor)
                cursor+=timedelta(days=1)
            if data.qualifying_dates is None:
                raise LeaveBalanceError('Additional leave requires documented qualifying dates')
            qualified=set(data.qualifying_dates)
            if not qualified <= planned:
                raise LeaveBalanceError('Qualifying work must reconcile to the documented working calendar')
            if any(day<account.accrual_start or day>through for day in qualified):
                raise LeaveBalanceError('Qualifying dates fall outside the accrued interval')
            cursor=account.accrual_start
            while cursor<=through:
                if cursor not in qualified:
                    exclusions.add(cursor)
                cursor+=timedelta(days=1)
        elif data.qualifying_dates is not None:
            raise LeaveBalanceError('Qualifying-work dates apply only to additional leave')
        try:
            amounts=annual_entitlement(year_start=account.period_start,year_end=account.period_end,through=through,
                segments=[EntitlementSegment(date.fromisoformat(s['start']),date.fromisoformat(s['end']),Decimal(s['annual_days'])) for s in basis['segments']],
                excluded_dates=exclusions,calendar_exclusions=calendar_exclusions)
        except ValueError as exc:
            raise LeaveBalanceError(str(exc)) from exc
    previous=dict((await db.execute(select(Entry.acquisition_bucket,func.sum(Entry.days)).where(
        Entry.company_id==company_id,Entry.account_id==account.id,Entry.kind=='accrual').group_by(Entry.acquisition_bucket))).all())
    entries=[]
    for index,bucket in enumerate(('before_2024','from_2024'),start=1):
        cumulative=amounts[bucket].quantize(Decimal('.00000001'),rounding=ROUND_HALF_UP)
        delta=cumulative-previous.get(bucket,Decimal(0))
        if delta<0:
            raise LeaveBalanceError('Accrued entitlement cannot be reduced by replacing source dates')
        if delta:
            entries.append(_entry(company_id=company_id,account_id=account.id,kind='accrual',
                effective_date=data.through,bucket=bucket,days=delta,reference=data.source_reference,
                request_key=data.request_key,fingerprint=fingerprint,line_no=index,actor_id=actor_id))
    async with db.begin_nested():
        document=Accrual(company_id=company_id,account_id=account.id,through=data.through,
            source_snapshot=data.model_dump(mode='json'),request_key=data.request_key,
            request_fingerprint=fingerprint,created_by=actor_id)
        db.add(document);await db.flush()
        for entry in entries:
            entry.request_key='accrual:'+str(document.id)
        db.add_all(entries);await db.flush()
    return entries


@serialized_payroll_mutation(LeaveBalanceError)
async def leave_balances(db, *, company_id, employment_contract_id, as_of):
    contract=await db.scalar(select(EmploymentContract.id).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==employment_contract_id))
    if contract is None:
        raise LeaveBalanceError('Employment contract not found in company')
    accounts=list((await db.scalars(select(Account).where(Account.company_id==company_id,
        Account.employment_contract_id==employment_contract_id).order_by(Account.period_start,Account.id))).all())
    result=[]
    for account in accounts:
        buckets=dict((await db.execute(select(Entry.acquisition_bucket,func.sum(Entry.days)).where(
            Entry.company_id==company_id,Entry.account_id==account.id,Entry.effective_date<=as_of)
            .group_by(Entry.acquisition_bucket))).all())
        result.append(dict(account_id=account.id,leave_kind=account.leave_kind,
            period_start=account.period_start,period_end=account.period_end,
            before_2024=buckets.get('before_2024',Decimal(0)),from_2024=buckets.get('from_2024',Decimal(0))))
    return result


@serialized_payroll_mutation(LeaveBalanceError)
async def allocate_leave_days(db, *, company_id, leave_request_id, allocations,
                             request_key, source_reference, actor_id):
    """Reserve documented day rights for a pending/approved paid leave request.

    Explicit account allocations preserve the working-year and acquisition
    provenance. Borrowed days need a separate authorization path, never an
    implicit negative balance.
    """
    from app.models.leave_request import LeaveRequest, PAID_VACATION_TYPES
    payload=dict(leave_request_id=leave_request_id,allocations=[{
        'account_id':a['account_id'],'bucket':a['bucket'],'days':str(a['days'])} for a in allocations],
        source_reference=source_reference)
    fingerprint=generate_request_fingerprint(payload)
    old=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,Entry.request_key==request_key))).all())
    if old:
        if any(row.request_fingerprint!=fingerprint for row in old):
            raise LeaveBalanceError('Request key contains different leave allocation')
        return old
    leave=await db.scalar(select(LeaveRequest).where(LeaveRequest.company_id==company_id,LeaveRequest.id==leave_request_id))
    if leave is None or leave.status not in ('pending','approved') or leave.leave_type not in PAID_VACATION_TYPES:
        raise LeaveBalanceError('Pending or approved paid leave required')
    existing=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave.id,Entry.kind=='use'))).all())
    reversed_ids=set((await db.scalars(select(Entry.reversal_of_id).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave.id,Entry.kind=='reversal'))).all())
    if any(row.id not in reversed_ids for row in existing):
        raise LeaveBalanceError('Leave already has an active allocation')
    if not allocations or not request_key.strip() or not source_reference.strip():
        raise LeaveBalanceError('Allocation and supporting evidence required')
    seen=set();total=Decimal(0);entries=[]
    for index,item in enumerate(allocations,start=1):
        account=await require_account(db,company_id,item['account_id'])
        kind=getattr(leave.leave_type,'value',leave.leave_type)
        if account.employment_contract_id!=leave.employment_contract_id or account.leave_kind!=kind:
            raise LeaveBalanceError('Allocation account belongs to different employment or leave kind')
        bucket=item['bucket'];amount=Decimal(item['days'])
        if bucket not in ('before_2024','from_2024') or not amount.is_finite() or amount<=0 or amount!=amount.to_integral_value():
            raise LeaveBalanceError('Leave use requires positive whole calendar days')
        if (account.id,bucket) in seen:
            raise LeaveBalanceError('Duplicate allocation account and bucket')
        seen.add((account.id,bucket))
        latest=await db.scalar(select(func.max(Entry.effective_date)).where(Entry.company_id==company_id,Entry.account_id==account.id))
        if latest and latest>leave.start_date:
            raise LeaveBalanceError('Leave allocation cannot precede recorded account movements')
        available=await db.scalar(select(func.coalesce(func.sum(Entry.days),0)).where(
            Entry.company_id==company_id,Entry.account_id==account.id,
            Entry.acquisition_bucket==bucket,Entry.effective_date<=leave.start_date))
        if amount>available:
            raise LeaveBalanceError('Insufficient accrued leave days; advance authorization is required')
        total+=amount
        entries.append(_entry(company_id=company_id,account_id=account.id,kind='use',
            effective_date=leave.start_date,bucket=bucket,days=-amount,reference=source_reference,
            request_key=request_key,fingerprint=fingerprint,line_no=index,actor_id=actor_id,
            leave_request_id=leave.id))
    if total!=(leave.end_date-leave.start_date).days+1:
        raise LeaveBalanceError('Allocated days do not match the leave calendar')
    db.add_all(entries);await db.flush()
    return entries


@serialized_payroll_mutation(LeaveBalanceError)
async def reverse_leave_allocation(db, *, company_id, leave_request_id, effective_date,
                                  request_key, source_reference, actor_id):
    """Return reserved days through compensating entries after leave cancellation."""
    from app.models.leave_request import LeaveRequest
    if not request_key.strip() or not source_reference.strip():
        raise LeaveBalanceError('Reversal request key and supporting evidence required')
    fingerprint=generate_request_fingerprint(dict(leave_request_id=leave_request_id,
        effective_date=effective_date.isoformat(),source_reference=source_reference))
    old=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,Entry.request_key==request_key))).all())
    if old:
        if any(row.request_fingerprint!=fingerprint for row in old):
            raise LeaveBalanceError('Request key contains different allocation reversal')
        return old
    leave=await db.scalar(select(LeaveRequest).where(LeaveRequest.company_id==company_id,LeaveRequest.id==leave_request_id))
    if leave is None or leave.status not in ('cancelled','rejected'):
        raise LeaveBalanceError('Cancel or reject the leave before returning reserved days')
    sources=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave.id,Entry.kind=='use').order_by(Entry.id))).all())
    already=set((await db.scalars(select(Entry.reversal_of_id).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave.id,Entry.kind=='reversal'))).all())
    entries=[]
    for source in sources:
        if source.id in already:
            continue
        if effective_date<source.effective_date:
            raise LeaveBalanceError('Reversal cannot predate original leave allocation')
        from app.models.payroll_leave_compensation import PayrollLeaveCompensation, PayrollLeaveCompensationLine
        from app.services.payroll_leave_compensation_service import active_compensation_filter
        if await db.scalar(select(PayrollLeaveCompensation.id).join(PayrollLeaveCompensationLine,
            (PayrollLeaveCompensationLine.company_id == PayrollLeaveCompensation.company_id) &
            (PayrollLeaveCompensationLine.compensation_id == PayrollLeaveCompensation.id)).where(
                PayrollLeaveCompensation.company_id == company_id,
                PayrollLeaveCompensationLine.account_id == source.account_id,
                active_compensation_filter()).limit(1)):
            raise LeaveBalanceError('Reverse dependent termination compensation before returning leave days')
        latest = await db.scalar(select(func.max(Entry.effective_date)).where(
            Entry.company_id == company_id, Entry.account_id == source.account_id))
        if latest is not None and effective_date < latest:
            raise LeaveBalanceError('Reversal cannot precede later leave movements')

        entries.append(_entry(company_id=company_id,account_id=source.account_id,kind='reversal',
            effective_date=effective_date,bucket=source.acquisition_bucket,days=-source.days,
            reference=source_reference,request_key=request_key,fingerprint=fingerprint,
            line_no=len(entries)+1,actor_id=actor_id,leave_request_id=leave.id,reversal_of_id=source.id))
    if not entries:
        raise LeaveBalanceError('No active allocation to reverse')
    db.add_all(entries);await db.flush()
    return entries


async def require_leave_allocation(db, *, company_id, leave_request):
    from app.models.leave_request import PAID_VACATION_TYPES
    if leave_request.leave_type not in PAID_VACATION_TYPES:
        return
    rows=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave_request.id))).all())
    amount=-sum((row.days for row in rows if row.kind in ('use','reversal')),Decimal(0))
    if amount!=(leave_request.end_date-leave_request.start_date).days+1:
        raise LeaveBalanceError('Allocate documented leave-day rights before approving paid leave')


async def return_cancelled_leave_days(db, *, company_id, leave_request, actor_id):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    rows=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave_request.id))).all())
    reversed_ids={row.reversal_of_id for row in rows if row.kind=='reversal'}
    if any(row.kind=='use' and row.id not in reversed_ids for row in rows):
        return await reverse_leave_allocation(db,company_id=company_id,leave_request_id=leave_request.id,
            effective_date=max(leave_request.start_date,datetime.now(ZoneInfo('Europe/Kyiv')).date()),
            request_key='cancel-leave:'+str(leave_request.id),source_reference='Cancelled/rejected leave '+str(leave_request.id),actor_id=actor_id)
