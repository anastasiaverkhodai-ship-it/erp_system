"""Dated recognized payroll tax liabilities and attributed bank settlements."""
from datetime import date
from decimal import Decimal as D
from sqlalchemy import select, func, or_
from sqlalchemy.orm import aliased
from app.models.company import Company
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
from app.models.payroll_tax_remittance import PayrollTaxRemittance, PayrollTaxRemittanceLine
from app.services.payroll_tax_remittance_service import TaxRemittanceError, TaxRemittanceNotFoundError, ROLES


async def payroll_tax_balances(db, *, company_id: int, as_of: date):
    # Payroll writes, including manual-journal reversal, take this company
    # lock exclusively. Keep accrual and payment reads in one business state.
    company = await db.scalar(select(Company.id).where(Company.id == company_id)
        .with_for_update(read=True))
    if company is None:
        raise TaxRemittanceNotFoundError('Company not found')
    reversal = aliased(JournalEntry)
    accruals = (await db.execute(select(PayrollCalculation.id, EmploymentContract.employee_id,
            PayrollCalculation.employment_contract_id, PayrollPeriod.start_date,
            PayrollPeriod.end_date, PayrollStatutoryResultLine.component,
            PayrollStatutoryResultLine.amount, PayrollStatutoryResultLine.currency_code,
            JournalEntry.id.label('accrual_journal_id'))
        .select_from(JournalEntry)
        .join(PayrollCalculation, (PayrollCalculation.company_id == JournalEntry.company_id) &
            (PayrollCalculation.id == JournalEntry.payroll_calculation_id))
        .join(EmploymentContract, (EmploymentContract.company_id == PayrollCalculation.company_id) &
            (EmploymentContract.id == PayrollCalculation.employment_contract_id))
        .join(PayrollPeriod, (PayrollPeriod.company_id == PayrollCalculation.company_id) &
            (PayrollPeriod.id == PayrollCalculation.payroll_period_id))
        .join(PayrollStatutoryResult, (PayrollStatutoryResult.company_id == PayrollCalculation.company_id) &
            (PayrollStatutoryResult.payroll_calculation_id == PayrollCalculation.id))
        .join(PayrollStatutoryResultLine, (PayrollStatutoryResultLine.company_id == PayrollStatutoryResult.company_id) &
            (PayrollStatutoryResultLine.payroll_statutory_result_id == PayrollStatutoryResult.id))
        .outerjoin(reversal, (reversal.company_id == JournalEntry.company_id) &
            (reversal.reversal_of_id == JournalEntry.id) & (reversal.status == 'posted'))
        .where(JournalEntry.company_id == company_id, JournalEntry.reversal_of_id.is_(None),
            JournalEntry.status.in_(('posted','reversed')), JournalEntry.entry_date <= as_of,
            or_(reversal.id.is_(None), reversal.entry_date > as_of))
        .order_by(PayrollCalculation.id, PayrollStatutoryResultLine.component)
        .limit(10001))).all()
    if len(accruals) > 10000:
        raise TaxRemittanceError('Tax balance exceeds 10000 component rows')
    lines = PayrollTaxRemittanceLine
    header = PayrollTaxRemittance
    payments = (await db.execute(select(lines.payroll_calculation_id, header.component,
            func.sum(lines.amount))
        .select_from(lines)
        .join(header, (header.company_id == lines.company_id) & (header.id == lines.remittance_id))
        .join(JournalEntry, (JournalEntry.company_id == header.company_id) & (JournalEntry.id == header.journal_entry_id))
        .outerjoin(reversal, (reversal.company_id == JournalEntry.company_id) &
            (reversal.reversal_of_id == JournalEntry.id) & (reversal.status == 'posted'))
        .where(lines.company_id == company_id, JournalEntry.status.in_(('posted','reversed')),
            JournalEntry.entry_date <= as_of, or_(reversal.id.is_(None), reversal.entry_date > as_of))
        .group_by(lines.payroll_calculation_id, header.component))).all()
    paid = {(calculation_id, component): amount for calculation_id, component, amount in payments}
    rows = {}
    for item in accruals:
        if item.component not in ROLES or item.currency_code != 'UAH':
            raise TaxRemittanceError('Tax balance contains unsupported component or currency')
        key = (item.id, item.component)
        row = rows.setdefault(key, dict(payroll_calculation_id=item.id, employee_id=item.employee_id,
            employment_contract_id=item.employment_contract_id, period_start=item.start_date,
            period_end=item.end_date, component=item.component, accrued_amount=D(0),
            paid_amount=paid.pop(key, D(0)), accrual_journal_id=item.accrual_journal_id))
        row['accrued_amount'] += item.amount
    if paid:
        # Never silently drop a payment whose recognized source is missing.
        raise TaxRemittanceError('Attributed tax payment has no active accrual at the requested date')
    totals = {component: dict(component=component, accrued_amount=D(0), paid_amount=D(0),
        outstanding_amount=D(0)) for component in ROLES}
    for row in rows.values():
        row['outstanding_amount'] = row['accrued_amount'] - row['paid_amount']
        if row['outstanding_amount'] < 0:
            raise TaxRemittanceError('Attributed tax payments exceed the recognized balance')
        for field in ('accrued_amount','paid_amount','outstanding_amount'):
            totals[row['component']][field] += row[field]
    return dict(company_id=company_id, as_of=as_of, currency_code='UAH',
        scope='posted_payroll_and_attributed_bank_payments', rows=list(rows.values()),
        totals=list(totals.values()))
