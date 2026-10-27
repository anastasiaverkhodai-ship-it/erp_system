"""Server-derived financial evidence for sick-pay corrections.

A linked payroll revision inherits actual historical settlements; changing a
calculation must never manufacture a repayment or make the old payment vanish.
"""
from decimal import Decimal
from sqlalchemy import select
from app.models.payroll import PayrollCalculation
from app.models.payroll_correction import PayrollCorrection
from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision
from app.services.payroll_sick_correction_plan import PayrollFinancialSnapshot


class SickFinancialSourceError(ValueError):
    pass


async def sick_settlement_chain(db, *, company_id, calculation_id):
    """Follow only financial carry-forward links, checking native lineage.

    Other correction types require original settlements to be cancelled or
    reversed, so they intentionally do not inherit active settlement balances.
    """
    ids = []
    current_id = calculation_id
    while current_id is not None:
        if current_id in ids:
            raise SickFinancialSourceError('Cyclic payroll correction lineage')
        current = await db.scalar(select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id, PayrollCalculation.id == current_id))
        if current is None:
            raise SickFinancialSourceError('Payroll correction calculation not found')
        ids.append(current.id)
        edge = await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.company_id == company_id,
            PayrollSickPayrollRevision.replacement_calculation_id == current.id))
        if edge is None:
            break
        parent = await db.scalar(select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == edge.original_calculation_id))
        link = await db.scalar(select(PayrollCorrection.id).where(
            PayrollCorrection.company_id == company_id,
            PayrollCorrection.original_payroll_calculation_id == edge.original_calculation_id,
            PayrollCorrection.replacement_payroll_calculation_id == current.id))
        if (parent is None or link is None or current.revision != parent.revision + 1
                or any(getattr(current, name) != getattr(parent, name) for name in
                    ('payroll_period_id', 'payroll_input_id', 'employment_contract_id', 'currency_code'))):
            raise SickFinancialSourceError('Invalid financial payroll correction lineage')
        current_id = parent.id
    return tuple(ids)


async def load_financial_snapshot(db, *, company_id, calculation_id):
    from app.services.payroll_accounting_service import (
        _load_sources, _component_amounts, _validate_source_reconciliation)
    from app.services.payroll_sick_accounting_service import sick_funding_for_calculation
    calculation, period, statutory, lines = await _load_sources(db,
        company_id=company_id, payroll_calculation_id=calculation_id)
    components = _component_amounts(lines)
    _validate_source_reconciliation(calculation=calculation, statutory=statutory, components=components)
    _, insurer = await sick_funding_for_calculation(db, company_id=company_id,
        calculation=calculation, period=period)
    result = PayrollFinancialSnapshot(gross=Decimal(calculation.gross_amount),
        employer_gross=Decimal(calculation.gross_amount)-insurer, insurer_gross=insurer,
        personal_income_tax=components['personal_income_tax'], military_levy=components['military_levy'],
        employer_contribution=components['unified_social_contribution'], net=Decimal(statutory.net_amount))
    result.validate()
    return result


async def recognized_sick_chain(db, *, company_id, calculation_id, as_of, error_type=ValueError):
    """Return active accrual journals for a complete signed correction chain."""
    from sqlalchemy.orm import selectinload
    from app.models.journal_entry import JournalEntry
    from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication
    ids = await sick_settlement_chain(db, company_id=company_id, calculation_id=calculation_id)
    journals = []
    for source_id in ids:
        edge = await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.company_id == company_id,
            PayrollSickPayrollRevision.replacement_calculation_id == source_id))
        if edge is not None:
            application = await db.scalar(select(PayrollSickCorrectionApplication).where(
                PayrollSickCorrectionApplication.company_id == company_id,
                PayrollSickCorrectionApplication.id == edge.application_id))
            if application is None or application.correction_date > as_of:
                raise error_type('Settlement cannot precede the sick correction')
        journal = await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_calculation_id == source_id,
            JournalEntry.reversal_of_id.is_(None)))
        if edge is not None and edge.journal_entry_id is None:
            if journal is not None or edge.original_financial != edge.replacement_financial:
                raise error_type('Zero sick correction does not reconcile to financial evidence')
            continue
        if (journal is None or journal.status != 'posted' or journal.entry_date > as_of
                or (edge is not None and edge.journal_entry_id != journal.id)):
            raise error_type('Post payroll accrual and its complete correction before settlement')
        journals.append(journal)
    if not journals:
        raise error_type('Recognized payroll accrual is required')
    return journals
