"""Dated attribution of actual advance tax payments to their payroll month.

The immutable advance already identifies the contract and month. Reading that
identity avoids a second payment/GL entry and keeps later revisions consistent.
"""
from sqlalchemy import select, or_
from sqlalchemy.orm import aliased
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_advance_tax_remittance import PayrollAdvanceTaxRemittance
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry


async def advance_tax_payment_rows(db, *, company_id, calculation_id=None, as_of=None):
    reversal = aliased(JournalEntry)
    payment = PayrollAdvanceTaxRemittance
    query = (select(payment.amount, JournalEntry.entry_date.label('payment_date'),
                    reversal.entry_date.label('reversal_date'), payment.component,
                    PayrollAdvance.payroll_period_id, PayrollAdvance.employment_contract_id,
                    EmploymentContract.employee_id, PayrollPeriod.start_date, PayrollPeriod.end_date)
        .select_from(payment)
        .join(PayrollAdvance, (PayrollAdvance.company_id == payment.company_id) &
              (PayrollAdvance.id == payment.payroll_advance_id))
        .join(EmploymentContract, (EmploymentContract.company_id == payment.company_id) &
              (EmploymentContract.id == PayrollAdvance.employment_contract_id))
        .join(PayrollPeriod, (PayrollPeriod.company_id == payment.company_id) &
              (PayrollPeriod.id == PayrollAdvance.payroll_period_id))
        .join(JournalEntry, (JournalEntry.company_id == payment.company_id) &
              (JournalEntry.id == payment.journal_entry_id))
        .outerjoin(reversal, (reversal.company_id == payment.company_id) &
              (reversal.reversal_of_id == JournalEntry.id) & (reversal.status == 'posted'))
        .where(payment.company_id == company_id, JournalEntry.reversal_of_id.is_(None),
               JournalEntry.status.in_(('posted', 'reversed'))))
    if calculation_id is not None:
        query = query.join(PayrollCalculation,
            (PayrollCalculation.company_id == payment.company_id) &
            (PayrollCalculation.payroll_period_id == PayrollAdvance.payroll_period_id) &
            (PayrollCalculation.employment_contract_id == PayrollAdvance.employment_contract_id)
        ).where(PayrollCalculation.id == calculation_id)
    if as_of is not None:
        query = query.where(JournalEntry.entry_date <= as_of,
            or_(reversal.id.is_(None), reversal.entry_date > as_of))
    return (await db.execute(query)).all()
