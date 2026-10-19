"""Authorize borrowed annual days without inventing accrued entitlement."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.payroll_leave_balance import PayrollLeaveBalanceEntry as Entry, PayrollLeaveAccrual as Accrual, PayrollLeaveAccount as Account
from app.schemas.payroll_leave_advance import LeaveAdvanceAuthorization
from app.services.payroll_leave_entitlement_math import annual_entitlement, EntitlementSegment


def add_months(day, count):
    month=day.year*12+day.month-1+count
    year,month=month//12,month%12+1
    return date(year,month,min(day.day,monthrange(year,month)[1]))


async def authorize_advance(db, *, company_id, account, leave, bucket, amount, available, authorization):
    from app.services.payroll_leave_balance_service import LeaveBalanceError
    try:
        document=LeaveAdvanceAuthorization.model_validate(authorization)
    except ValueError as exc:
        raise LeaveBalanceError('Invalid documented advance authorization') from exc
    if account.leave_kind not in {'annual','additional'}:
        raise LeaveBalanceError('Social leave cannot borrow rights from another year')
    if not account.period_start<=leave.start_date<=account.period_end:
        raise LeaveBalanceError('Advance leave must belong to the current working year')
    contract=await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id==company_id,EmploymentContract.id==account.employment_contract_id))
    if contract is None or contract.status=='cancelled' or leave.start_date<contract.start_date or (
            contract.end_date and leave.end_date>contract.end_date):
        raise LeaveBalanceError('Advance leave falls outside valid employment')
    eligibility=dict(contract_start_date=contract.start_date.isoformat(),working_year_start=account.period_start.isoformat())
    if document.document_date>leave.start_date:
        raise LeaveBalanceError('Advance authorization cannot postdate the leave')
    if document.reason=='six_months' and leave.start_date<add_months(contract.start_date,6):
        raise LeaveBalanceError('Six months of employment have not elapsed')
    if document.reason=='subsequent_year' and account.period_start<add_months(contract.start_date,12):
        raise LeaveBalanceError('The first working year requires six months or a documented exception')
    if document.reason=='disability':
        accounts=list((await db.scalars(select(Account).where(Account.company_id==company_id,
            Account.employment_contract_id==contract.id,Account.leave_kind=='annual',
            Account.period_start<=leave.start_date,Account.period_end>=leave.start_date))).all())
        proofs=[(source.id,part) for source in accounts for part in source.basis['segments']
                if part.get('disability_group') and
                date.fromisoformat(part['start'])<=leave.start_date<=date.fromisoformat(part['end'])]
        if len(proofs)!=1:
            raise LeaveBalanceError('Advance disability exception requires a dated disability entitlement source')
        eligibility['disability_account_id']=proofs[0][0]
        eligibility['disability_segment']=proofs[0][1]
    if document.reason=='minor':
        employee=await db.get(Employee,contract.employee_id)
        if employee.birth_date is None or employee.birth_date>leave.start_date or leave.start_date>=add_months(employee.birth_date,216):
            raise LeaveBalanceError('Minor exception requires a verified age below eighteen')
        eligibility['birth_date']=employee.birth_date.isoformat()
    if document.reason=='secondary_main_leave':
        from app.services.employment_event_service import employment_state_on
        state=await employment_state_on(db,company_id=company_id,contract_id=contract.id,on_date=leave.start_date)
        eligibility['employment_kind']=state['terms']['employment_kind']
        if state['terms']['employment_kind'] not in {'internal_secondary','external_secondary'}:
            raise LeaveBalanceError('Simultaneous primary leave requires secondary employment')
        if not document.main_leave_start<=leave.start_date<=leave.end_date<=document.main_leave_end:
            raise LeaveBalanceError('Advance days must fall within the documented primary leave')
    last=await db.scalar(select(Accrual).where(Accrual.company_id==company_id,Accrual.account_id==account.id)
        .order_by(Accrual.through.desc(),Accrual.id.desc()).limit(1))
    if last and last.through>leave.start_date:
        raise LeaveBalanceError('Advance cannot precede a recorded entitlement calculation')
    if leave.start_date>account.accrual_start and (last is None or last.through<leave.start_date-timedelta(days=1)):
        raise LeaveBalanceError('Accrue actual rights through the day before advance leave')
    basis=account.basis
    excluded={date.fromisoformat(day) for day in basis['excluded_service_dates']}
    calendar={date.fromisoformat(day) for day in basis['calendar_exclusions']}
    day=account.period_start
    while day<account.accrual_start:
        excluded.add(day);day+=timedelta(days=1)
    if account.leave_kind=='additional':
        planned={date.fromisoformat(day) for day in basis['planned_work_dates']}
        qualified={date.fromisoformat(day) for day in (last.source_snapshot.get('qualifying_dates') or [])} if last else set()
        day=account.period_start
        while day<=account.period_end:
            if day not in planned:
                calendar.add(day)
            if last and account.accrual_start<=day<=last.through and day not in qualified:
                excluded.add(day)
            day+=timedelta(days=1)
    projection=annual_entitlement(
        year_start=account.period_start,year_end=account.period_end,
        through=min(account.period_end,contract.end_date or account.period_end),
        segments=[EntitlementSegment(date.fromisoformat(part['start']),date.fromisoformat(part['end']),
                                     Decimal(part['annual_days'])) for part in basis['segments']],
        excluded_dates=excluded,calendar_exclusions=calendar)
    credited=dict((await db.execute(select(Entry.acquisition_bucket,func.sum(Entry.days)).where(
        Entry.company_id==company_id,Entry.account_id==account.id,Entry.kind=='accrual')
        .group_by(Entry.acquisition_bucket))).all())
    balances=dict((await db.execute(select(Entry.acquisition_bucket,func.sum(Entry.days)).where(
        Entry.company_id==company_id,Entry.account_id==account.id,Entry.effective_date<=leave.start_date)
        .group_by(Entry.acquisition_bucket))).all())
    future={key:max(Decimal(0),projection[key]-credited.get(key,Decimal(0)))
            for key in ('before_2024','from_2024')}
    remaining={key:balances.get(key,Decimal(0))+future[key] for key in future}
    rounded_old=remaining['before_2024'].quantize(Decimal(1),rounding=ROUND_HALF_UP)
    rounded_total=sum(remaining.values()).quantize(Decimal(1),rounding=ROUND_HALF_UP)
    ceiling=rounded_old if bucket=='before_2024' else rounded_total-rounded_old
    if amount>ceiling:
        raise LeaveBalanceError('Advance exceeds the remaining full working-year entitlement')
    return dict(authorization=document.model_dump(mode='json'),eligibility=eligibility,rule='UA_LEAVE_ARTICLE_10_ADVANCE_V1',
                accrued_balance=str(available),projected_future_days=str(future[bucket]),
                projected_remaining_days=str(ceiling),borrowed_days=str(max(Decimal(0),amount-available)))
