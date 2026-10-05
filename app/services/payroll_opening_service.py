"""Verified payroll opening detail and immutable historical calculation inputs."""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func, and_
from app.models.company import Company
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.opening_balance import OpeningBalance
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_opening import PayrollOpeningPackage, PayrollOpeningDebt, PayrollEarningsHistory, PayrollLeaveOpening
from app.services.payroll_mutation_guard import serialized_payroll_mutation, ensure_payroll_source_editable
from app.services.idempotency_fingerprint_service import generate_request_fingerprint
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import (
    resolve_company_account_roles, AccountingAccountRoleResolutionError,
)


class PayrollOpeningError(Exception): pass
class PayrollOpeningNotFoundError(PayrollOpeningError): pass


async def _contract(db,company_id,contract_id):
    row=await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==contract_id).execution_options(populate_existing=True))
    if row is None: raise PayrollOpeningNotFoundError('Employment contract not found')
    if row.status=='cancelled': raise PayrollOpeningError('Cancelled employment cannot receive opening data')
    return row


async def list_payroll_opening_debts(db, *, company_id, opening_balance_id):
    return list((await db.scalars(select(PayrollOpeningDebt).join(PayrollOpeningPackage,
        and_(PayrollOpeningPackage.company_id==company_id,PayrollOpeningPackage.id==PayrollOpeningDebt.package_id))
        .where(PayrollOpeningDebt.company_id==company_id,PayrollOpeningPackage.opening_balance_id==opening_balance_id)
        .order_by(PayrollOpeningDebt.employee_id))).all())


@serialized_payroll_mutation(PayrollOpeningError)
async def attach_payroll_opening(db, *, company_id, opening_balance_id, data, created_by):
    fingerprint=generate_request_fingerprint(dict(opening_balance_id=opening_balance_id,
        source_reference=data.source_reference,debts=sorted([row.model_dump(mode='json') for row in data.debts],key=lambda row:row['employee_id'])))
    existing=await db.scalar(select(PayrollOpeningPackage).where(PayrollOpeningPackage.company_id==company_id,
        PayrollOpeningPackage.request_key==data.request_key))
    if existing:
        if existing.request_fingerprint!=fingerprint:
            raise PayrollOpeningError('Request key was used for different opening detail')
        return existing
    opening=await db.scalar(select(OpeningBalance).where(OpeningBalance.company_id==company_id,
        OpeningBalance.id==opening_balance_id))
    if opening is None: raise PayrollOpeningNotFoundError('Opening balance not found')
    journal=await db.scalar(select(JournalEntry).where(JournalEntry.company_id==company_id,
        JournalEntry.id==opening.journal_entry_id).with_for_update().execution_options(populate_existing=True))
    if journal is None or journal.status!='posted':
        raise PayrollOpeningError('Payroll opening detail requires a posted opening journal')
    if await db.scalar(select(PayrollOpeningPackage.id).where(PayrollOpeningPackage.company_id==company_id,
        PayrollOpeningPackage.opening_balance_id==opening.id)):
        raise PayrollOpeningError('Opening journal already has payroll detail')
    employee_ids={row.employee_id for row in data.debts}
    found=set((await db.scalars(select(Employee.id).where(Employee.company_id==company_id,Employee.id.in_(employee_ids)))).all())
    if found!=employee_ids: raise PayrollOpeningError('Invalid or foreign-company employee')
    if await db.scalar(select(PayrollOpeningDebt.id).where(PayrollOpeningDebt.company_id==company_id,
        PayrollOpeningDebt.employee_id.in_(employee_ids)).limit(1)):
        raise PayrollOpeningError('Employee already has an opening payroll balance')
    try:
        accounts=await resolve_company_account_roles(db,company_id=company_id,roles=(Role.PAYROLL_NET_PAYABLE,))
    except AccountingAccountRoleResolutionError as exc:
        raise PayrollOpeningError(str(exc)) from exc
    debit,credit=(await db.execute(select(func.coalesce(func.sum(JournalEntryLine.debit),0),
        func.coalesce(func.sum(JournalEntryLine.credit),0)).where(JournalEntryLine.journal_entry_id==journal.id,
        JournalEntryLine.account_id==accounts[Role.PAYROLL_NET_PAYABLE].id))).one()
    positive=sum((row.net_amount for row in data.debts if row.net_amount>0),Decimal(0))
    negative=-sum((row.net_amount for row in data.debts if row.net_amount<0),Decimal(0))
    if positive!=credit or negative!=debit:
        raise PayrollOpeningError('Employee opening debts must reconcile separately to payroll debit and credit')
    package=PayrollOpeningPackage(company_id=company_id,opening_balance_id=opening.id,
        request_key=data.request_key,request_fingerprint=fingerprint,source_reference=data.source_reference,created_by=created_by)
    async with db.begin_nested():
        db.add(package);await db.flush()
        db.add_all([PayrollOpeningDebt(company_id=company_id,package_id=package.id,employee_id=row.employee_id,
            net_amount=row.net_amount,currency_code='UAH',as_of=opening.opening_date) for row in data.debts])
        await db.flush()
    return package


@serialized_payroll_mutation(PayrollOpeningError)
async def import_earnings_history(db, *, company_id, contract_id, data, created_by):
    contract=await _contract(db,company_id,contract_id)
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    existing=await db.scalar(select(PayrollEarningsHistory).where(PayrollEarningsHistory.company_id==company_id,
        PayrollEarningsHistory.employment_contract_id==contract_id,PayrollEarningsHistory.month==data.month))
    if existing:
        if existing.request_fingerprint!=fingerprint: raise PayrollOpeningError('Historical month already has different data')
        return existing
    if data.cutover_date>date.today(): raise PayrollOpeningError('Cutover date cannot be in the future')
    month_end=data.month.replace(day=monthrange(data.month.year,data.month.month)[1])
    if month_end<contract.start_date or (contract.end_date and data.month>contract.end_date):
        raise PayrollOpeningError('Historical month falls outside employment')
    employed_days=(min(month_end,contract.end_date or month_end)-max(data.month,contract.start_date)).days+1
    if max(data.vacation_days,data.sick_days)>employed_days:
        raise PayrollOpeningError('Historical eligible days exceed employment days in month')
    other_cutover=await db.scalar(select(PayrollEarningsHistory.id).where(
        PayrollEarningsHistory.company_id==company_id,PayrollEarningsHistory.employment_contract_id==contract_id,
        PayrollEarningsHistory.cutover_date!=data.cutover_date).limit(1))
    if other_cutover is not None: raise PayrollOpeningError('Use one cutover date for earnings history')

    if await db.scalar(select(PayrollCalculation.id).join(PayrollPeriod,
        PayrollPeriod.id==PayrollCalculation.payroll_period_id).where(PayrollCalculation.company_id==company_id,
        PayrollCalculation.employment_contract_id==contract_id,PayrollPeriod.start_date<=month_end,
        PayrollPeriod.end_date>=data.month).limit(1)):
        raise PayrollOpeningError('Historical import overlaps an existing ERP calculation')
    await ensure_payroll_source_editable(db,company_id=company_id,date_from=data.cutover_date,
        contract_id=contract_id,error_type=PayrollOpeningError)
    row=PayrollEarningsHistory(company_id=company_id,employment_contract_id=contract_id,
        **data.model_dump(),request_fingerprint=fingerprint,created_by=created_by)
    db.add(row);await db.flush();return row


@serialized_payroll_mutation(PayrollOpeningError)
async def import_leave_opening(db, *, company_id, contract_id, data, created_by):
    contract=await _contract(db,company_id,contract_id)
    fingerprint=generate_request_fingerprint(data.model_dump(mode='json'))
    existing=await db.scalar(select(PayrollLeaveOpening).where(PayrollLeaveOpening.company_id==company_id,
        PayrollLeaveOpening.employment_contract_id==contract_id,PayrollLeaveOpening.leave_type==data.leave_type,
        PayrollLeaveOpening.working_year_start==data.working_year_start))
    if existing:
        if existing.request_fingerprint!=fingerprint: raise PayrollOpeningError('Working year already has different opening leave data')
        return existing
    if data.as_of>date.today() or data.working_year_start<contract.start_date or (contract.end_date and data.as_of>contract.end_date):
        raise PayrollOpeningError('Invalid opening leave date for employment')
    other_date=await db.scalar(select(PayrollLeaveOpening.id).where(PayrollLeaveOpening.company_id==company_id,
        PayrollLeaveOpening.employment_contract_id==contract_id,PayrollLeaveOpening.as_of!=data.as_of).limit(1))
    if other_date is not None: raise PayrollOpeningError('Use one opening date for leave balances')
    await ensure_payroll_source_editable(db,company_id=company_id,date_from=data.as_of,
        contract_id=contract_id,error_type=PayrollOpeningError)
    row=PayrollLeaveOpening(company_id=company_id,employment_contract_id=contract_id,
        **data.model_dump(),request_fingerprint=fingerprint,created_by=created_by)
    db.add(row);await db.flush();return row


async def ensure_no_payroll_opening_detail(db,company_id,opening_id,error_type):
    if await db.scalar(select(PayrollOpeningPackage.id).where(PayrollOpeningPackage.company_id==company_id,
        PayrollOpeningPackage.opening_balance_id==opening_id)):
        raise error_type('Payroll opening detail requires an explicit correction before opening reversal')
