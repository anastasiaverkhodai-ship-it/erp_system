"""Link termination leave compensation to the leave ledger and payroll input."""
import json
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from app.models.employment_event import EmploymentEvent
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollInput, PayrollPeriod, PayrollCalculation
from app.models.payroll_opening import PayrollLeaveOpening
from app.models.payroll_leave_balance import PayrollLeaveAccount as Account, PayrollLeaveBalanceEntry as Entry, PayrollLeaveAccrual as Accrual
from app.models.payroll_leave_compensation import PayrollLeaveCompensation as Compensation, PayrollLeaveCompensationLine as Line, PayrollLeaveCompensationReversal as Reversal
from app.services.payroll_leave_balance_service import LeaveBalanceError, leave_balances, _entry
from app.services.payroll_leave_compensation_basis_service import derive_compensation_average
from app.services.payroll_leave_entitlement_math import compensation_amount
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


@serialized_payroll_mutation(LeaveBalanceError)
async def create_leave_compensation(db, *, company_id, data, actor_id):
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    old=await db.scalar(select(Compensation).where(Compensation.company_id==company_id,Compensation.request_key==data.request_key))
    if old is not None:
        if old.request_fingerprint!=fingerprint:
            raise LeaveBalanceError('Request key contains different compensation data')
        return old
    event=await db.scalar(select(EmploymentEvent).where(EmploymentEvent.company_id==company_id,
        EmploymentEvent.id==data.termination_event_id,EmploymentEvent.event_type=='termination'))
    if event is None or await db.scalar(select(EmploymentEvent.id).where(EmploymentEvent.company_id==company_id,
        EmploymentEvent.reversal_of_id==data.termination_event_id)):
        raise LeaveBalanceError('Active documented termination required')
    contract=await db.get(EmploymentContract,event.employment_contract_id)
    if contract.status!='ended' or contract.end_date!=event.effective_date:
        raise LeaveBalanceError('Termination does not match current employment state')
    if await db.scalar(select(Compensation.id).where(Compensation.company_id==company_id,Compensation.termination_event_id==event.id, active_compensation_filter())):
        raise LeaveBalanceError('Termination already has a compensation calculation')
    payroll_input=await db.scalar(select(PayrollInput).where(PayrollInput.company_id==company_id,
        PayrollInput.id==data.payroll_input_id,PayrollInput.employment_contract_id==contract.id))
    if payroll_input is None:
        raise LeaveBalanceError('Payroll input for the terminated employment required')
    period=await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id==company_id,PayrollPeriod.id==payroll_input.payroll_period_id))
    if period.status!='draft' or not period.start_date<=event.effective_date<=period.end_date:
        raise LeaveBalanceError('Open termination payroll period required')
    if await db.scalar(select(PayrollCalculation.id).where(PayrollCalculation.company_id==company_id,
        PayrollCalculation.payroll_input_id==payroll_input.id).limit(1)):
        raise LeaveBalanceError('Calculate compensation before payroll; existing calculations require correction')
    accounts=list((await db.scalars(select(Account).where(Account.company_id==company_id,
        Account.employment_contract_id==contract.id))).all())
    if not accounts or not any(account.leave_kind=='annual' for account in accounts):
        raise LeaveBalanceError('Document all annual leave rights before final settlement')
    openings=list((await db.scalars(select(PayrollLeaveOpening.id).where(PayrollLeaveOpening.company_id==company_id,
        PayrollLeaveOpening.employment_contract_id==contract.id))).all())
    if set(openings)-{account.opening_id for account in accounts}:
        raise LeaveBalanceError('Unassigned imported leave rights remain')
    from app.models.leave_request import LeaveRequest, PAID_VACATION_TYPES
    leaves=list((await db.scalars(select(LeaveRequest).where(LeaveRequest.company_id==company_id,
        LeaveRequest.employment_contract_id==contract.id,LeaveRequest.leave_type.in_(PAID_VACATION_TYPES),
        LeaveRequest.status.in_(['pending','approved'])))).all())
    for leave in leaves:
        relevant=[a for a in accounts if a.leave_kind==getattr(leave.leave_type,'value',leave.leave_type)]
        if leave.status=='pending' or leave.end_date>event.effective_date:
            raise LeaveBalanceError('Resolve pending or future paid leave before final settlement')
        imported_through=[]
        for account in relevant:
            if account.opening_id:
                opening=await db.get(PayrollLeaveOpening,account.opening_id)
                imported_through.append(opening.as_of)
        if imported_through and leave.end_date<=min(imported_through):
            continue
        used=await db.scalar(select(func.coalesce(func.sum(Entry.days),0)).where(Entry.company_id==company_id,
            Entry.leave_request_id==leave.id))
        if -used!=(leave.end_date-leave.start_date).days+1:
            raise LeaveBalanceError('Reconcile historical paid leave to day balances first')
    for account in accounts:
        required=min(account.period_end,event.effective_date)
        if account.accrual_start<=required:
            through=await db.scalar(select(func.max(Accrual.through)).where(Accrual.company_id==company_id,Accrual.account_id==account.id))
            if through is None or through<required:
                raise LeaveBalanceError('Accrue leave rights through termination before compensation')
        latest=await db.scalar(select(func.max(Entry.effective_date)).where(Entry.company_id==company_id,Entry.account_id==account.id))
        if latest and latest>event.effective_date:
            raise LeaveBalanceError('Future leave ledger entries must be resolved first')
    balances=await leave_balances(db,company_id=company_id,employment_contract_id=contract.id,as_of=event.effective_date)
    prepared=[];bases={};exempt_balances=[]
    for balance in balances:
        before=balance['before_2024'];after=balance['from_2024']
        if before<0 or after<0:
            from app.schemas.employment_termination_basis import EmploymentTerminationBasis, EXEMPT_GROUNDS
            try:
                termination_basis=EmploymentTerminationBasis.model_validate(
                    (event.request_payload or {}).get('termination_basis'))
            except ValueError as exc:
                raise LeaveBalanceError('Overused leave requires a documented recovery or exemption decision') from exc
            if termination_basis.ground not in EXEMPT_GROUNDS or balance['leave_kind'] not in {'annual','additional'}:
                raise LeaveBalanceError('Overused leave requires a documented recovery or exemption decision')
            exempt_balances.append(dict(account_id=balance['account_id'],
                before_2024=str(min(before,Decimal(0))),from_2024=str(min(after,Decimal(0)))))
            # The statutory exemption is not newly earned leave. Preserve the
            # historical negative ledger and settle only independently owed rights.
            before=max(before,Decimal(0));after=max(after,Decimal(0))
        total=(before+after).quantize(Decimal('1'),rounding=ROUND_HALF_UP)
        rounded_before=before.quantize(Decimal('1'),rounding=ROUND_HALF_UP)
        for bucket,consumed,quantity in [('before_2024',before,rounded_before),('from_2024',after,total-rounded_before)]:
            if not consumed:
                continue
            if bucket not in bases:
                bases[bucket]=await derive_compensation_average(db,company_id=company_id,
                    employment_contract_id=contract.id,compensation_date=event.effective_date,acquisition_bucket=bucket,
                    salary_substitution=data.salary_substitution)
            basis=bases[bucket]
            amount=compensation_amount(days=quantity,eligible_earnings=basis['eligible_earnings'],eligible_days=basis['eligible_days'])
            prepared.append((balance['account_id'],bucket,consumed,quantity,amount,basis))
    async with db.begin_nested():
        row=Compensation(company_id=company_id,employment_contract_id=contract.id,termination_event_id=event.id,
            payroll_input_id=payroll_input.id,compensation_date=event.effective_date,
            recovery_exemption=(dict(rule='UA_LEAVE_ART22_EXEMPTION_V1',
                termination_event_id=event.id,termination_basis=termination_basis.model_dump(mode='json'),
                balances=exempt_balances) if exempt_balances else None),
            gross_amount=sum((item[4] for item in prepared),Decimal(0)),source_reference=data.entitlement_review_reference,
            request_key=data.request_key,request_fingerprint=fingerprint,created_by=actor_id)
        db.add(row);await db.flush()
        for index,(account_id,bucket,consumed,quantity,amount,basis) in enumerate(prepared,start=1):
            entry=_entry(company_id=company_id,account_id=account_id,kind='compensation',effective_date=event.effective_date,
                bucket=bucket,days=-consumed,reference=data.entitlement_review_reference,request_key='compensation:'+str(row.id),
                fingerprint=fingerprint,line_no=index,actor_id=actor_id,termination_event_id=event.id)
            db.add(entry);await db.flush()
            db.add(Line(company_id=company_id,compensation_id=row.id,account_id=account_id,balance_entry_id=entry.id,
                acquisition_bucket=bucket,consumed_days=consumed,compensated_days=quantity,amount=amount,
                basis_snapshot=json.loads(json.dumps(dict(basis,rounding='cumulative-half-up-whole-days'),default=str))))
        await db.flush()
    return row


async def derive_compensation_payroll_lines(db, *, company_id, payroll_input, period):
    headers=list((await db.scalars(select(Compensation).where(
        Compensation.company_id==company_id,Compensation.payroll_input_id==payroll_input.id,
        active_compensation_filter()).order_by(Compensation.id))).all())
    result=[]
    from app.services.payroll_leave_correction_sources import latest_leave_correction
    for header in headers:
        if not period.start_date<=header.compensation_date<=period.end_date:
            raise LeaveBalanceError('Compensation falls outside its payroll period')
        correction=await latest_leave_correction(db,company_id=company_id,target_kind='compensation',target_id=header.id)
        if correction:
            for item in correction.replacement_snapshot['lines']:
                if not Decimal(item['quantity']):continue
                field='source_leave_compensation_extra_id' if item.get('source_kind')=='extra' else 'source_leave_compensation_line_id'
                result.append(dict(line_type='leave_compensation',quantity=Decimal(item['quantity']),
                    rate=Decimal(item['rate']),amount=Decimal(item['amount']),currency_code='UAH',**{field:item['source_id']}))
        else:
            lines=list((await db.scalars(select(Line).where(Line.company_id==company_id,Line.compensation_id==header.id).order_by(Line.id))).all())
            for line in lines:
                if not line.compensated_days:continue
                result.append(dict(line_type='leave_compensation',quantity=line.compensated_days,
                    rate=(line.amount/line.compensated_days).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP),
                    amount=line.amount,currency_code='UAH',source_leave_compensation_line_id=line.id))
    return result


def active_compensation_filter():
    return ~select(Reversal.id).where(Reversal.company_id == Compensation.company_id,
        Reversal.compensation_id == Compensation.id).exists()


@serialized_payroll_mutation(LeaveBalanceError)
async def reverse_leave_compensation(db, *, company_id, compensation_id, data, actor_id):
    fingerprint = generate_request_fingerprint(dict(data.model_dump(mode='json'), compensation_id=compensation_id))
    previous = await db.scalar(select(Reversal).where(Reversal.company_id == company_id,
        Reversal.request_key == data.request_key))
    if previous is not None:
        if previous.request_fingerprint != fingerprint:
            raise LeaveBalanceError('Request key contains different reversal data')
        return previous
    header = await db.scalar(select(Compensation).where(Compensation.company_id == company_id,
        Compensation.id == compensation_id, active_compensation_filter()))
    if header is None:
        raise LeaveBalanceError('Active leave compensation not found')
    from app.services.payroll_leave_correction_sources import latest_leave_correction
    if await latest_leave_correction(db,company_id=company_id,target_kind='compensation',target_id=header.id):
        raise LeaveBalanceError('Reviewed compensation history requires a further reviewed correction')
    payroll_input = await db.get(PayrollInput, header.payroll_input_id)
    period = await db.get(PayrollPeriod, payroll_input.payroll_period_id)
    if period.status != 'draft':
        raise LeaveBalanceError('Reopen payroll through its correction workflow before reversing compensation')
    if await db.scalar(select(PayrollCalculation.id).where(PayrollCalculation.company_id == company_id,
        PayrollCalculation.payroll_input_id == header.payroll_input_id).limit(1)):
        raise LeaveBalanceError('Compensation used in payroll requires a payroll correction')
    if data.effective_date < header.compensation_date:
        raise LeaveBalanceError('Reversal cannot precede compensation')
    lines = list((await db.scalars(select(Line).where(Line.company_id == company_id,
        Line.compensation_id == header.id).order_by(Line.id))).all())
    for line in lines:
        latest = await db.scalar(select(func.max(Entry.effective_date)).where(
            Entry.company_id == company_id, Entry.account_id == line.account_id))
        if latest is not None and latest > data.effective_date:
            raise LeaveBalanceError('Reversal cannot precede later leave movements')
    async with db.begin_nested():
        row = Reversal(company_id=company_id, compensation_id=header.id,
            effective_date=data.effective_date, source_reference=data.source_reference,
            request_key=data.request_key, request_fingerprint=fingerprint, created_by=actor_id)
        db.add(row)
        await db.flush()
        for index, line in enumerate(lines, start=1):
            db.add(_entry(company_id=company_id, account_id=line.account_id, kind='reversal',
                effective_date=data.effective_date, bucket=line.acquisition_bucket,
                days=line.consumed_days, reference=data.source_reference,
                request_key='compensation-reversal:' + str(row.id), fingerprint=fingerprint,
                line_no=index, actor_id=actor_id, reversal_of_id=line.balance_entry_id,
                termination_event_id=header.termination_event_id))
        await db.flush()
    return row
