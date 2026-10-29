"""Read immutable leave correction snapshots without changing historical sources."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.models.payroll_leave_correction import PayrollLeaveCorrection
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication


async def latest_leave_correction(db, *, company_id, target_kind, target_id):
    return await db.scalar(select(PayrollLeaveCorrection).where(
        PayrollLeaveCorrection.company_id==company_id,
        PayrollLeaveCorrection.target_kind==target_kind,
        PayrollLeaveCorrection.target_id==target_id)
        .order_by(PayrollLeaveCorrection.revision.desc()).limit(1))


def vacation_snapshot(source, leave):
    from app.services.payroll_leave_change_plan import LeaveRange
    return dict(kind='vacation',target_id=source.id,contract_id=source.employment_contract_id,
        leave_request_id=leave.id,start=leave.start_date.isoformat(),end=leave.end_date.isoformat(),
        original_amount=format(Decimal(source.vacation_pay_amount).quantize(Decimal('.01')),'f'),
        rate=format(Decimal(source.average_daily_amount).quantize(Decimal('.000001')),'f'),
        used_dates=[day.isoformat() for day in LeaveRange(leave.start_date,leave.end_date).dates()])


def vacation_values(snapshot, period_start, period_end):
    from app.services.payroll_leave_allocation import allocate_leave_amount
    start,end=date.fromisoformat(snapshot['start']),date.fromisoformat(snapshot['end'])
    days=0;amount=Decimal('0.00')
    for value in snapshot['used_dates']:
        day=date.fromisoformat(value)
        if period_start<=day<=period_end:
            count,part=allocate_leave_amount(start=start,end=end,period_start=day,period_end=day,
                amount=Decimal(snapshot['original_amount']))
            if count!=1: raise ValueError('Corrected leave day falls outside original source')
            days+=1;amount+=part
    return dict(line_type='vacation_pay',source_id=snapshot['target_id'],
        quantity=Decimal(days),amount=amount,rate=Decimal(snapshot['rate']))


async def calculation_leave_snapshot(db, *, company_id, calculation_id, target_kind, target_id):
    """Resolve only evidence in this calculation's ancestry, never later changes."""
    from app.models.payroll_correction import PayrollCorrection
    # Native ancestry also covers an initial unposted payroll correction.
    from app.models.payroll import PayrollCalculation
    seen=set();current=calculation_id;bound_id=None
    while current is not None:
        if current in seen:raise ValueError('Cyclic leave calculation history')
        seen.add(current)
        calculation=await db.scalar(select(PayrollCalculation).where(
            PayrollCalculation.company_id==company_id,PayrollCalculation.id==current))
        if calculation is None:raise ValueError('Leave calculation not found')
        rows=(await db.execute(select(PayrollLeaveCorrection,PayrollSickCorrectionApplication)
            .join(PayrollSickCorrectionApplication,
                (PayrollSickCorrectionApplication.company_id==PayrollLeaveCorrection.company_id)&
                (PayrollSickCorrectionApplication.id==PayrollLeaveCorrection.application_id))
            .where(PayrollLeaveCorrection.company_id==company_id,
                PayrollLeaveCorrection.target_kind==target_kind,PayrollLeaveCorrection.target_id==target_id)
            .order_by(PayrollLeaveCorrection.revision.desc()))).all()
        for correction,application in rows:
            if current in application.payroll_calculation_ids:
                return correction.replacement_snapshot
        if bound_id is None:
            bound_id=(calculation.minimum_wage_assessment or {}).get('correction_basis',{}).get(
                'payload',{}).get('leave_correction_sources',{}).get(f'{target_kind}:{target_id}')
        current=await db.scalar(select(PayrollCorrection.original_payroll_calculation_id).where(
            PayrollCorrection.company_id==company_id,
            PayrollCorrection.replacement_payroll_calculation_id==current))
    if bound_id is not None:
        for correction,_ in rows:
            if correction.id==bound_id:return correction.replacement_snapshot
        raise ValueError('Invalid bound leave correction source')
    return None


async def bind_leave_sources(db, *, company_id, lines):
    """Freeze the source revision used by an ordinary new calculation."""
    from app.models.payroll_leave_compensation import PayrollLeaveCompensationLine
    keys=set()
    for line in lines:
        if line.get('source_vacation_calculation_id'):
            keys.add(('vacation',line['source_vacation_calculation_id']))
        if line.get('source_leave_compensation_extra_id'):
            from app.models.payroll_leave_correction import PayrollLeaveCompensationExtraSource
            target=await db.scalar(select(PayrollLeaveCompensationExtraSource.compensation_id).where(
                PayrollLeaveCompensationExtraSource.company_id==company_id,
                PayrollLeaveCompensationExtraSource.id==line['source_leave_compensation_extra_id']))
            if target is None:raise ValueError('Additional compensation source not found')
            keys.add(('compensation',target))
        if line.get('source_leave_compensation_line_id'):
            target=await db.scalar(select(PayrollLeaveCompensationLine.compensation_id).where(
                PayrollLeaveCompensationLine.company_id==company_id,
                PayrollLeaveCompensationLine.id==line['source_leave_compensation_line_id']))
            if target is None:raise ValueError('Compensation source not found')
            keys.add(('compensation',target))
    result={}
    for kind,target in sorted(keys):
        evidence=await latest_leave_correction(db,company_id=company_id,target_kind=kind,target_id=target)
        if evidence is not None:result[f'{kind}:{target}']=evidence.id
    return result


async def leave_read_view(db, row):
    """Expose the immutable original order alongside its effective calendar."""
    from app.models.payroll_vacation import PayrollVacationCalculation
    from app.schemas.leave_request import LeaveRequestRead
    result=LeaveRequestRead.model_validate(row)
    source=await db.scalar(select(PayrollVacationCalculation).where(
        PayrollVacationCalculation.company_id==row.company_id,
        PayrollVacationCalculation.leave_request_id==row.id))
    if source is not None:
        correction=await latest_leave_correction(db,company_id=row.company_id,target_kind='vacation',target_id=source.id)
        if correction is not None:
            result.correction_id=correction.id
            result.effective_leave_dates=[date.fromisoformat(value) for value in correction.replacement_snapshot['used_dates']]
    return result
