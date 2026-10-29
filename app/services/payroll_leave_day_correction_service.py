"""Return only documented unused calendar days to their original right buckets."""
from datetime import date,timedelta
from decimal import Decimal
from sqlalchemy import select,func
from app.models.payroll_leave_balance import PayrollLeaveBalanceEntry as Entry
from app.models.payroll_leave_correction import PayrollLeaveDayCorrection as Day,PayrollLeaveCorrection as Correction
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication as Application


def return_query():
    return select(Day,Entry,Application).join(Entry,
        (Entry.company_id==Day.company_id)&(Entry.id==Day.allocation_id)).join(Correction,
        (Correction.company_id==Day.company_id)&(Correction.id==Day.correction_id)).join(Application,
        (Application.company_id==Correction.company_id)&(Application.id==Correction.application_id))


async def corrected_right_days(db, *, company_id, account_id, bucket, as_of):
    rows=(await db.execute(return_query().where(Day.company_id==company_id,Entry.account_id==account_id,
        Entry.acquisition_bucket==bucket,Entry.effective_date<=as_of,Application.correction_date<=as_of))).all()
    result=sum((Decimal(row.days) for row,_,_ in rows),Decimal(0))
    corrections=list((await db.scalars(select(Correction).join(Application,
        (Application.company_id==Correction.company_id)&(Application.id==Correction.application_id))
        .where(Correction.company_id==company_id,Correction.target_kind=='compensation',
            Application.correction_date<=as_of))).all())
    for correction in corrections:
        for sign,snapshot in ((1,correction.original_snapshot),(-1,correction.replacement_snapshot)):
            result+=sign*sum((Decimal(item['consumed_days']) for item in snapshot['lines']
                if item['account_id']==account_id and item['bucket']==bucket),Decimal(0))
    return result


async def release_leave_days(db, *, company_id, correction, leave_request_id):
    original=correction.original_snapshot;replacement=correction.replacement_snapshot
    released=set(original['used_dates'])-set(replacement['used_dates'])
    rows=list((await db.scalars(select(Entry).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave_request_id,Entry.kind=='use').order_by(Entry.id))).all())
    reversals=set((await db.scalars(select(Entry.reversal_of_id).where(Entry.company_id==company_id,
        Entry.leave_request_id==leave_request_id,Entry.kind=='reversal'))).all())
    rows=[row for row in rows if row.id not in reversals]
    start=date.fromisoformat(original['start']);end=date.fromisoformat(original['end'])
    if sum((-row.days for row in rows),Decimal(0))!=(end-start).days+1:
        raise ValueError('Original leave rights must reconcile before returning unused days')
    offset=0
    for row in rows:
        quantity=int(-row.days)
        if Decimal(quantity)!=-row.days:raise ValueError('Leave allocation must use whole days')
        dates={(start+timedelta(days=i)).isoformat() for i in range(offset,offset+quantity)}
        count=len(dates&released);offset+=quantity
        if count:
            previous=await db.scalar(select(func.coalesce(func.sum(Day.days),0)).where(
                Day.company_id==company_id,Day.allocation_id==row.id))
            if previous+count>quantity:raise ValueError('Leave-day return exceeds original allocation')
            db.add(Day(company_id=company_id,correction_id=correction.id,allocation_id=row.id,days=count))
    await db.flush()


async def transfer_released_days(db, *, company_id, correction, ranges, actor_id):
    from app.models.leave_request import LeaveRequest
    from app.models.payroll_leave_correction import PayrollLeaveTransfer
    from app.schemas.leave_request import LeaveRequestCreate
    from app.services.leave_request_service import create_leave_request,approve_leave_request
    from app.services.payroll_leave_balance_service import allocate_leave_days
    original=await db.get(LeaveRequest,correction.original_snapshot['leave_request_id'])
    rows=(await db.execute(return_query().where(Day.company_id==company_id,Day.correction_id==correction.id)
        .order_by(Entry.id))).all()
    grouped={}
    for day,entry,_ in rows:
        key=(entry.account_id,entry.acquisition_bucket)
        grouped[key]=grouped.get(key,0)+day.days
    available=[dict(account_id=account_id,bucket=bucket,days=days)
        for (account_id,bucket),days in grouped.items()]
    result=[]
    for segment in ranges:
        remaining=(segment.end-segment.start).days+1;allocations=[]
        for item in available:
            take=min(item['days'],remaining)
            if take:
                allocations.append(dict(account_id=item['account_id'],bucket=item['bucket'],days=Decimal(take)))
                item['days']-=take;remaining-=take
        if remaining:raise ValueError('Transferred leave exceeds released day rights')
        leave=await create_leave_request(db,company_id=company_id,requested_by=actor_id,data=LeaveRequestCreate(
            employment_contract_id=original.employment_contract_id,leave_type=original.leave_type,
            start_date=segment.start,end_date=segment.end,reason=f'Transfer under leave correction {correction.id}'))
        await allocate_leave_days(db,company_id=company_id,leave_request_id=leave.id,allocations=allocations,
            request_key=f'leave-transfer:{correction.id}:{leave.id}',source_reference=f'Reviewed leave correction {correction.id}',actor_id=actor_id)
        await approve_leave_request(db,company_id=company_id,leave_request_id=leave.id,approved_by=actor_id)
        db.add(PayrollLeaveTransfer(company_id=company_id,correction_id=correction.id,leave_request_id=leave.id))
        result.append(leave.id)
    await db.flush()
    return result
