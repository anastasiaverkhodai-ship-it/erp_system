"""Dated employee settlement balances from opening detail, accruals and payouts."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select, and_
from app.models.company import Company
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_opening import PayrollOpeningDebt,PayrollOpeningPackage
from app.models.opening_balance import OpeningBalance
from app.models.journal_entry import JournalEntry
from app.services.payroll_opening_service import PayrollOpeningNotFoundError


async def employee_payroll_balance(db, *, company_id:int, employee_id:int, as_of:date):
    await db.scalar(select(Company.id).where(Company.id==company_id).with_for_update(read=True))
    employee=await db.scalar(select(Employee.id).where(Employee.company_id==company_id,Employee.id==employee_id))
    if employee is None: raise PayrollOpeningNotFoundError('Employee not found')
    currencies={}
    issues=[]
    def totals(currency):
        return currencies.setdefault(currency,dict(currency_code=currency,opening_amount=Decimal(0),
            accrued_net=Decimal(0),paid_net=Decimal(0)))
    openings=(await db.execute(select(PayrollOpeningDebt,JournalEntry.status)
        .join(PayrollOpeningPackage,and_(PayrollOpeningPackage.company_id==company_id,PayrollOpeningPackage.id==PayrollOpeningDebt.package_id))
        .join(OpeningBalance,and_(OpeningBalance.company_id==company_id,OpeningBalance.id==PayrollOpeningPackage.opening_balance_id))
        .join(JournalEntry,and_(JournalEntry.company_id==company_id,JournalEntry.id==OpeningBalance.journal_entry_id))
        .where(PayrollOpeningDebt.company_id==company_id,PayrollOpeningDebt.employee_id==employee_id,
               PayrollOpeningDebt.as_of<=as_of))).all()
    for debt,status in openings:
        totals(debt.currency_code)['opening_amount']+=debt.net_amount
        if status!='posted': issues.append('opening_journal_not_active')
    accruals=(await db.execute(select(JournalEntry,PayrollStatutoryResult)
        .join(PayrollCalculation,and_(PayrollCalculation.company_id==company_id,PayrollCalculation.id==JournalEntry.payroll_calculation_id))
        .join(EmploymentContract,and_(EmploymentContract.company_id==company_id,EmploymentContract.id==PayrollCalculation.employment_contract_id))
        .outerjoin(PayrollStatutoryResult,and_(PayrollStatutoryResult.company_id==company_id,PayrollStatutoryResult.payroll_calculation_id==PayrollCalculation.id))
        .where(JournalEntry.company_id==company_id,EmploymentContract.employee_id==employee_id,
            JournalEntry.status.in_(('posted','reversed')),JournalEntry.entry_date<=as_of))).all()
    for journal,result in accruals:
        if result is None:
            issues.append('posted_accrual_missing_statutory_result')
            continue
        sign=-1 if journal.reversal_of_id is not None else 1
        totals(result.currency_code)['accrued_net']+=sign*result.net_amount
    payouts=(await db.scalars(select(PayrollDisbursement).where(PayrollDisbursement.company_id==company_id,
        PayrollDisbursement.employee_id==employee_id,PayrollDisbursement.confirmed_at.is_not(None),
        PayrollDisbursement.payment_date<=as_of))).all()
    for payout in payouts:
        if payout.reversed_on is None or payout.reversed_on>as_of:
            totals(payout.currency_code)['paid_net']+=payout.amount
    for row in currencies.values():
        row['balance']=None if issues else row['opening_amount']+row['accrued_net']-row['paid_net']
    return dict(company_id=company_id,employee_id=employee_id,as_of=as_of,complete=not issues,
        issues=sorted(set(issues)),balances=[currencies[key] for key in sorted(currencies)])
