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
    from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision, PayrollSickCorrectionApplication
    edges = list((await db.scalars(select(PayrollSickPayrollRevision)
        .join(PayrollSickCorrectionApplication,
            (PayrollSickCorrectionApplication.company_id==PayrollSickPayrollRevision.company_id) &
            (PayrollSickCorrectionApplication.id==PayrollSickPayrollRevision.application_id))
        .where(PayrollSickPayrollRevision.company_id==company_id,
            PayrollSickCorrectionApplication.correction_date<=as_of))).all())
    successor = {edge.original_calculation_id: edge.replacement_calculation_id for edge in edges}
    by_journal = {edge.journal_entry_id: edge for edge in edges if edge.journal_entry_id is not None}
    def current_at(identifier):
        seen = set()
        while identifier in successor:
            if identifier in seen:
                raise TaxRemittanceError('Cyclic sick tax correction lineage')
            seen.add(identifier)
            identifier = successor[identifier]
        return identifier
    paid = {}
    for calculation_id, component, amount in payments:
        key = (current_at(calculation_id), component)
        paid[key] = paid.get(key,D(0))+amount
    rows = {}
    snapshot_components = dict(personal_income_tax='personal_income_tax', military_levy='military_levy',
        unified_social_contribution='employer_contribution')
    counted_differences = set()
    for item in accruals:
        if item.component not in ROLES or item.currency_code != 'UAH':
            raise TaxRemittanceError('Tax balance contains unsupported component or currency')
        key = (current_at(item.id), item.component)
        row = rows.setdefault(key, dict(payroll_calculation_id=key[0], employee_id=item.employee_id,
            employment_contract_id=item.employment_contract_id, period_start=item.start_date,
            period_end=item.end_date, component=item.component, accrued_amount=D(0),
            paid_amount=paid.pop(key, D(0)), accrual_journal_id=item.accrual_journal_id))
        edge = by_journal.get(item.accrual_journal_id)
        if edge is None:
            row['accrued_amount'] += item.amount
        else:
            difference_key = (edge.id, item.component)
            if difference_key in counted_differences:
                continue
            counted_differences.add(difference_key)
            field = snapshot_components[item.component]
            row['accrued_amount'] += D(edge.replacement_financial[field])-D(edge.original_financial[field])
    if paid:
        # Never silently drop a payment whose recognized source is missing.
        raise TaxRemittanceError('Attributed tax payment has no active accrual at the requested date')
    # Advance payments are already posted to the tax liability account. Match
    # their immutable contract/month once; no duplicate monthly payment exists.
    from app.services.payroll_advance_tax_settlement import advance_tax_payment_rows
    advance_keys = set()
    for payment in await advance_tax_payment_rows(db, company_id=company_id, as_of=as_of):
        matches = [(key, row) for key, row in rows.items()
                   if row['employment_contract_id'] == payment.employment_contract_id
                   and row['period_start'] == payment.start_date
                   and row['period_end'] == payment.end_date
                   and row['component'] == payment.component]
        if len(matches) > 1:
            raise TaxRemittanceError('Ambiguous monthly accrual for advance tax payment')
        if matches:
            key, row = matches[0]
        else:
            key = ('advance', payment.employment_contract_id, payment.payroll_period_id, payment.component)
            row = rows.setdefault(key, dict(payroll_calculation_id=None,
                employee_id=payment.employee_id, employment_contract_id=payment.employment_contract_id,
                period_start=payment.start_date, period_end=payment.end_date,
                component=payment.component, accrued_amount=D(0), paid_amount=D(0), accrual_journal_id=None))
        row['paid_amount'] += payment.amount
        advance_keys.add(key)
    totals = {component: dict(component=component, accrued_amount=D(0), paid_amount=D(0),
        outstanding_amount=D(0), overpaid_amount=D(0)) for component in ROLES}
    for key, row in rows.items():
        row['outstanding_amount'] = row['accrued_amount'] - row['paid_amount']
        row['overpaid_amount'] = max(D(0), -row['outstanding_amount'])
        if row['overpaid_amount'] and key not in advance_keys and row['payroll_calculation_id'] not in successor.values():
            raise TaxRemittanceError('Attributed tax payments exceed the recognized balance')
        row['outstanding_amount'] = max(D(0), row['outstanding_amount'])
        for field in ('accrued_amount','paid_amount','outstanding_amount','overpaid_amount'):
            totals[row['component']][field] += row[field]
    return dict(company_id=company_id, as_of=as_of, currency_code='UAH',
        scope='posted_payroll_and_attributed_bank_payments', rows=list(rows.values()),
        totals=list(totals.values()))
