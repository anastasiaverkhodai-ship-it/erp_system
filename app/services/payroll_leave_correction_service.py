"""Reviewed leave requests and atomic financial revisions.

The initiator is retained on the application; the actual approver is retained
on its immutable leave evidence. Actual historical payments remain untouched.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from app.models.payroll_leave_correction import PayrollLeaveCorrection, PayrollLeaveCorrectionRequest
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication, PayrollSickPayrollRevision
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.employment_contract import EmploymentContract
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.idempotency_fingerprint_service import generate_request_fingerprint
from app.services.payroll_revision_service import current_calculation_filter
from app.services.payroll_leave_correction_sources import latest_leave_correction, vacation_snapshot, vacation_values
from app.schemas.payroll_leave_correction import LeaveCorrectionRequestCreate


class LeaveCorrectionError(ValueError):pass


@serialized_payroll_mutation(LeaveCorrectionError)
async def request_leave_correction(db, *, company_id, actor_id, data):
    payload=data.model_dump(mode='json')
    fingerprint=generate_request_fingerprint(payload)
    old=await db.scalar(select(PayrollLeaveCorrectionRequest).where(
        PayrollLeaveCorrectionRequest.company_id==company_id,PayrollLeaveCorrectionRequest.request_key==data.request_key))
    if old:
        if old.request_fingerprint!=fingerprint:raise LeaveCorrectionError('Request key has different correction data')
        return old
    # Resolve the actual source now; approval derives it again under the same
    # company serialization lock so a stale request cannot replace newer facts.
    old_snapshot,new_snapshot,_=await prepare_leave_source(db,company_id=company_id,data=data)
    payload['expected_source']=old_snapshot
    payload['expected_replacement']=new_snapshot
    row=PayrollLeaveCorrectionRequest(company_id=company_id,request_key=data.request_key,
        request_fingerprint=fingerprint,created_by=actor_id,payload=payload)
    db.add(row);await db.flush()
    return row


async def prepare_leave_source(db, *, company_id, data):
    from app.models.payroll_vacation import PayrollVacationCalculation
    from app.models.leave_request import LeaveRequest
    from app.models.payroll_leave_compensation import PayrollLeaveCompensation, PayrollLeaveCompensationLine
    previous=await latest_leave_correction(db,company_id=company_id,target_kind=data.target_kind,target_id=data.target_id)
    if previous is not None:
        previous_app=await db.scalar(select(PayrollSickCorrectionApplication).where(
            PayrollSickCorrectionApplication.company_id==company_id,
            PayrollSickCorrectionApplication.id==previous.application_id))
        if data.correction_date<previous_app.correction_date:
            raise LeaveCorrectionError('Correction cannot precede its previous revision')
    if data.target_kind=='vacation':
        source=await db.scalar(select(PayrollVacationCalculation).where(
            PayrollVacationCalculation.company_id==company_id,PayrollVacationCalculation.id==data.target_id))
        if source is None:raise LeaveCorrectionError('Vacation source not found')
        leave=await db.scalar(select(LeaveRequest).where(LeaveRequest.company_id==company_id,LeaveRequest.id==source.leave_request_id))
        if leave is None or leave.status!='approved':raise LeaveCorrectionError('Approved source leave required')
        original=previous.replacement_snapshot if previous else vacation_snapshot(source,leave)
        from app.services.payroll_leave_change_plan import LeaveRange, plan_leave_change
        plan=plan_leave_change(original=LeaveRange(leave.start_date,leave.end_date),
            used=tuple(LeaveRange(r.start,r.end) for r in data.retained_ranges),
            transferred=tuple(LeaveRange(r.start,r.end) for r in data.transferred_ranges),
            original_amount=source.vacation_pay_amount,leave_kind=getattr(leave.leave_type,'value',leave.leave_type),
            operation=data.operation,effective_date=data.effective_date,
            order_reference=data.order_reference,consent_reference=data.consent_reference,
            recall_ground=data.recall_ground,legal_reference=data.legal_reference)
        if getattr(leave.leave_type,'value',leave.leave_type) in {'annual','additional'}:
            segments=[*data.retained_ranges,*data.transferred_ranges]
            if not any((segment.end-segment.start).days+1>=14 for segment in segments) and not data.annual_continuity_reference:
                raise LeaveCorrectionError('Document the uninterrupted 14-day annual leave part in the working-year schedule')
        retained=[day.isoformat() for day in plan.used_dates]
        if not set(retained)<set(original['used_dates']):
            raise LeaveCorrectionError('Change must release currently retained leave dates')
        replacement=dict(original,used_dates=retained)
        return original,replacement,previous
    source=await db.scalar(select(PayrollLeaveCompensation).where(
        PayrollLeaveCompensation.company_id==company_id,PayrollLeaveCompensation.id==data.target_id))
    if source is None:raise LeaveCorrectionError('Compensation source not found')
    from app.services.payroll_leave_compensation_service import active_compensation_filter
    if not await db.scalar(select(PayrollLeaveCompensation.id).where(
        PayrollLeaveCompensation.company_id==company_id,PayrollLeaveCompensation.id==source.id,active_compensation_filter())):
        raise LeaveCorrectionError('Active compensation source required')
    from app.models.employment_event import EmploymentEvent
    event=await db.scalar(select(EmploymentEvent).where(EmploymentEvent.company_id==company_id,
        EmploymentEvent.id==source.termination_event_id,EmploymentEvent.event_type=='termination'))
    if event is None or event.effective_date!=source.compensation_date or await db.scalar(select(EmploymentEvent.id).where(
        EmploymentEvent.company_id==company_id,EmploymentEvent.reversal_of_id==source.termination_event_id)):
        raise LeaveCorrectionError('An active matching termination is required')
    from app.schemas.employment_termination_basis import EmploymentTerminationBasis,EXEMPT_GROUNDS
    exempt=False
    if source.recovery_exemption:
        basis=EmploymentTerminationBasis.model_validate((event.request_payload or {}).get('termination_basis'))
        exempt=basis.ground in EXEMPT_GROUNDS
    lines=list((await db.scalars(select(PayrollLeaveCompensationLine).where(
        PayrollLeaveCompensationLine.company_id==company_id,PayrollLeaveCompensationLine.compensation_id==source.id)
        .order_by(PayrollLeaveCompensationLine.id))).all())
    original=previous.replacement_snapshot if previous else dict(kind='compensation',target_id=source.id,
        contract_id=source.employment_contract_id,lines=[dict(source_id=line.id,account_id=line.account_id,
        bucket=line.acquisition_bucket,consumed_days=format(Decimal(line.consumed_days).quantize(Decimal('.00000001')),'f'),quantity=format(Decimal(line.compensated_days).quantize(Decimal('.01')),'f'),
        amount=format(Decimal(line.amount).quantize(Decimal('.01')),'f'),rate=format((line.amount/line.compensated_days if line.compensated_days else Decimal(0)).quantize(Decimal('.000001')),'f')) for line in lines])
    from app.services.payroll_leave_compensation_basis_service import derive_compensation_average
    from app.services.payroll_leave_entitlement_math import compensation_amount
    import json
    from app.services.payroll_leave_balance_service import leave_balances
    from app.models.payroll_leave_balance import PayrollLeaveBalanceEntry
    if data.correction_date<source.compensation_date:
        raise LeaveCorrectionError('Correction cannot precede compensation')
    future=await db.scalar(select(PayrollLeaveBalanceEntry.id).where(
        PayrollLeaveBalanceEntry.company_id==company_id,
        PayrollLeaveBalanceEntry.account_id.in_([line.account_id for line in lines]),
        PayrollLeaveBalanceEntry.effective_date>source.compensation_date).limit(1))
    if future is not None:raise LeaveCorrectionError('Resolve later leave movements before compensation correction')
    balances=await leave_balances(db,company_id=company_id,
        employment_contract_id=source.employment_contract_id,as_of=data.correction_date)
    from app.models.payroll_leave_correction import PayrollLeaveCompensationExtraSource as Extra
    lines.extend((await db.scalars(select(Extra).where(Extra.company_id==company_id,Extra.compensation_id==source.id))).all())
    def identity(line):return ('extra' if isinstance(line,Extra) else 'original',line.id)
    prior={(item.get('source_kind','original'),item['source_id']):item for item in original['lines']}
    quantities={};consumed={}
    for balance in balances:
        account_lines={line.acquisition_bucket:line for line in lines if line.account_id==balance['account_id']}
        available={}
        for bucket in ('before_2024','from_2024'):
            line=account_lines.get(bucket)
            previously=Decimal(prior.get(identity(line),{}).get('consumed_days',str(getattr(line,'consumed_days',0)))) if line else Decimal(0)
            available[bucket]=balance[bucket]+previously
            if available[bucket]<0:
                if exempt and balance['leave_kind'] in {'annual','additional'}:
                    available[bucket]=Decimal(0)
                else:
                    raise LeaveCorrectionError('Negative leave rights need a documented recovery or exemption')
            if available[bucket] and line is None:
                line=Extra(company_id=company_id,compensation_id=source.id,account_id=balance['account_id'],acquisition_bucket=bucket)
                db.add(line);await db.flush();lines.append(line);account_lines[bucket]=line
        before=available['before_2024'].quantize(Decimal(1),rounding=ROUND_HALF_UP)
        total=sum(available.values()).quantize(Decimal(1),rounding=ROUND_HALF_UP)
        for bucket,line in account_lines.items():
            consumed[identity(line)]=available[bucket]
            quantities[identity(line)]=before if bucket=='before_2024' else total-before
    derived=[];bases={}
    for line in lines:
        if line.acquisition_bucket not in bases:
            bases[line.acquisition_bucket]=await derive_compensation_average(db,company_id=company_id,
                employment_contract_id=source.employment_contract_id,compensation_date=source.compensation_date,
                acquisition_bucket=line.acquisition_bucket,salary_substitution=data.salary_substitution)
        basis=bases[line.acquisition_bucket]
        quantity=quantities[identity(line)]
        amount=compensation_amount(days=quantity,eligible_earnings=basis['eligible_earnings'],eligible_days=basis['eligible_days'])
        derived.append(dict(source_id=line.id,source_kind=identity(line)[0],account_id=line.account_id,bucket=line.acquisition_bucket,
            consumed_days=str(consumed[identity(line)]),quantity=str(quantity),amount=str(amount),
            rate=str((amount/quantity).quantize(Decimal('.000001')) if quantity else Decimal(0))))
    replacement=dict(original,lines=derived,bases=json.loads(json.dumps(bases,default=str)))
    return original,replacement,previous


@serialized_payroll_mutation(LeaveCorrectionError)
async def apply_leave_correction(db, *, company_id, request_id, actor_id):
    request=await db.scalar(select(PayrollLeaveCorrectionRequest).where(
        PayrollLeaveCorrectionRequest.company_id==company_id,PayrollLeaveCorrectionRequest.id==request_id))
    if request is None:raise LeaveCorrectionError('Leave correction request not found')
    if actor_id==request.created_by:raise LeaveCorrectionError('An independent reviewer must apply the correction')
    existing=await db.scalar(select(PayrollLeaveCorrection).where(
        PayrollLeaveCorrection.company_id==company_id,PayrollLeaveCorrection.request_id==request.id))
    if existing:return await db.get(PayrollSickCorrectionApplication,existing.application_id)
    payload=dict(request.payload);expected=payload.pop('expected_source');expected_new=payload.pop('expected_replacement')
    data=LeaveCorrectionRequestCreate.model_validate(payload)
    original,replacement,previous=await prepare_leave_source(db,company_id=company_id,data=data)
    if original!=expected or replacement!=expected_new:raise LeaveCorrectionError('Leave source changed after review request; submit a new request')
    from app.services.accounting_period_service import ensure_period_open
    await ensure_period_open(company_id,data.correction_date,db)
    source_field=(PayrollCalculationLine.source_vacation_calculation_id if data.target_kind=='vacation'
                  else PayrollCalculationLine.source_leave_compensation_line_id)
    source_ids=[data.target_id] if data.target_kind=='vacation' else [item['source_id'] for item in original['lines']]
    periods=select(PayrollCalculation.payroll_period_id).join(PayrollCalculationLine,
        (PayrollCalculationLine.company_id==PayrollCalculation.company_id)&
        (PayrollCalculationLine.payroll_calculation_id==PayrollCalculation.id)).where(
            PayrollCalculation.company_id==company_id,current_calculation_filter(),source_field.in_(source_ids))
    if data.target_kind=='compensation':
        from app.models.payroll_leave_compensation import PayrollLeaveCompensation
        compensation=await db.scalar(select(PayrollLeaveCompensation).where(
            PayrollLeaveCompensation.company_id==company_id,PayrollLeaveCompensation.id==data.target_id))
        periods=select(PayrollCalculation.payroll_period_id).where(
            PayrollCalculation.company_id==company_id,PayrollCalculation.payroll_input_id==compensation.payroll_input_id)
    contract=await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==original['contract_id']))
    affected=list((await db.scalars(select(PayrollCalculation).join(EmploymentContract,
        (EmploymentContract.company_id==PayrollCalculation.company_id)&
        (EmploymentContract.id==PayrollCalculation.employment_contract_id)).where(
        PayrollCalculation.company_id==company_id,current_calculation_filter(),
        EmploymentContract.employee_id==contract.employee_id,PayrollCalculation.payroll_period_id.in_(periods))
        .order_by(PayrollCalculation.payroll_period_id,PayrollCalculation.id))).all())
    from app.services.payroll_sick_correction_application_service import snapshot_json
    from app.services.payroll_sick_financial_sources import load_financial_snapshot
    from app.services.payroll_statutory_service import calculate_payroll_statutory_result
    from app.services.payroll_accounting_service import get_payroll_accounting_journal,generate_and_post_payroll_journal_entry
    from app.services.payroll_sick_payroll_revision_service import build_sick_payroll_revision
    from app.services.payroll_sick_correction_journal import build_sick_difference_journal
    from app.services.accounting_posting import post_journal_entry
    async with db.begin_nested():
        old_financial={};posted={};new_calcs=[]
        for calc in affected:
            period=await db.get(PayrollPeriod,calc.payroll_period_id)
            if data.correction_date<period.end_date:raise LeaveCorrectionError('Correction cannot precede the affected payroll month end')
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=calc.id,actor_user_id=actor_id)
            old_financial[calc.id]=await load_financial_snapshot(db,company_id=company_id,calculation_id=calc.id)
            journal=await get_payroll_accounting_journal(db,company_id=company_id,payroll_calculation_id=calc.id)
            if journal and (journal.status!='posted' or journal.entry_date>data.correction_date):
                raise LeaveCorrectionError('Original accrual must be posted before correction')
            posted[calc.id]=journal is not None
            owned=list((await db.scalars(select(source_field).where(
                PayrollCalculationLine.company_id==company_id,PayrollCalculationLine.payroll_calculation_id==calc.id,
                source_field.in_(source_ids)))).all())
            changes=[]
            if owned and data.target_kind=='vacation':changes=[vacation_values(replacement,period.start_date,period.end_date)]
            elif data.target_kind=='compensation' and calc.payroll_input_id==compensation.payroll_input_id:
                changes=[dict(line_type='leave_compensation',source_id=item['source_id'],
                    quantity=Decimal(item['quantity']),amount=Decimal(item['amount']),rate=Decimal(item['rate']),
                    source_kind=item.get('source_kind','original'),allow_new_compensation=True) for item in replacement['lines']]
            new=await build_sick_payroll_revision(db,company_id=company_id,original=calc,
                original_sick=None,replacement_sick=None,source_pairs=[],leave_replacements=changes,
                request_key=f'leave:{request.id}:{calc.id}',reason=data.reason,actor_id=actor_id)
            new_calcs.append((calc,new))
        app=PayrollSickCorrectionApplication(company_id=company_id,source_kind='leave',
            original_sick_id=None,replacement_sick_id=None,source_replacements={},
            payroll_calculation_ids=[new.id for _,new in new_calcs],request_key=f'leave:{request.id}',
            request_fingerprint=request.request_fingerprint,reason=data.reason,correction_date=data.correction_date,
            created_by=request.created_by)
        db.add(app);await db.flush()
        evidence=PayrollLeaveCorrection(company_id=company_id,application_id=app.id,request_id=request.id,
            target_kind=data.target_kind,target_id=data.target_id,revision=previous.revision+1 if previous else 1,
            supersedes_id=previous.id if previous else None,reviewed_by=actor_id,
            original_snapshot=original,replacement_snapshot=replacement)
        db.add(evidence);await db.flush()
        if data.target_kind=='vacation':
            from app.services.payroll_leave_day_correction_service import release_leave_days
            await release_leave_days(db,company_id=company_id,correction=evidence,
                leave_request_id=original['leave_request_id'])
            from app.services.payroll_leave_day_correction_service import transfer_released_days
            await transfer_released_days(db,company_id=company_id,correction=evidence,
                ranges=data.transferred_ranges,actor_id=actor_id)
        for calc,new in new_calcs:
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=new.id,actor_user_id=actor_id)
            snapshot=await load_financial_snapshot(db,company_id=company_id,calculation_id=new.id)
            if posted[calc.id]:
                journal=await build_sick_difference_journal(db,company_id=company_id,replacement_calculation_id=new.id,
                    original=old_financial[calc.id],replacement=snapshot,correction_date=data.correction_date,actor_id=actor_id,source_kind="leave")
                db.add(PayrollSickPayrollRevision(company_id=company_id,application_id=app.id,
                    original_calculation_id=calc.id,replacement_calculation_id=new.id,journal_entry_id=journal.id if journal else None,
                    original_financial=snapshot_json(old_financial[calc.id]),replacement_financial=snapshot_json(snapshot),
                    net_delta=snapshot.net-old_financial[calc.id].net))
                await db.flush()
                if journal:await post_journal_entry(db,company_id,journal.id)
            else:
                await generate_and_post_payroll_journal_entry(db,company_id=company_id,payroll_calculation_id=new.id,
                    created_by=actor_id,posting_date=data.correction_date)
        return app
