"""Attribute a real posted bank return; never fabricate recovery from wages."""
from decimal import Decimal as D
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.payroll_sick_return import PayrollSickReturn
from app.models.payroll import PayrollCalculation
from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision
from app.models.journal_entry import JournalEntry
from app.models.bank_account import BankAccount
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import require_current_calculation
from app.services.payroll_sick_financial_sources import recognized_sick_chain, sick_settlement_chain
from app.services.payroll_sick_settlement_service import sick_settlement_summary
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class SickReturnError(ValueError):
    pass


@serialized_payroll_mutation(SickReturnError)
async def register_sick_return(db, *, company_id, calculation_id, journal_entry_id,
                               bank_account_id, kind, request_key, document_reference, actor_id):
    if (kind not in {'employee','insurer'} or not isinstance(request_key,str)
            or not request_key.strip() or len(request_key)>100
            or not isinstance(document_reference,str) or not 10<=len(document_reference.strip())<=500):
        raise SickReturnError('A return type, request key and confirmed document reference are required')
    fingerprint = generate_request_fingerprint(dict(calculation_id=calculation_id,
        journal_entry_id=journal_entry_id, bank_account_id=bank_account_id,
        kind=kind, document_reference=document_reference.strip()))
    existing = await db.scalar(select(PayrollSickReturn).where(
        PayrollSickReturn.company_id==company_id, PayrollSickReturn.request_key==request_key))
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise SickReturnError('Return request key was used for different details')
        return existing
    calculation = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id==company_id, PayrollCalculation.id==calculation_id))
    if calculation is None:
        raise SickReturnError('Payroll calculation not found')
    await require_current_calculation(db, calculation=calculation, error_type=SickReturnError)
    edge = await db.scalar(select(PayrollSickPayrollRevision.id).where(
        PayrollSickPayrollRevision.company_id==company_id,
        PayrollSickPayrollRevision.replacement_calculation_id==calculation_id))
    if edge is None:
        raise SickReturnError('An applied financial sick correction is required')
    journal = await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id, JournalEntry.id==journal_entry_id).with_for_update())
    if journal is None or journal.status!='posted' or journal.reversal_of_id is not None:
        raise SickReturnError('An original posted bank return is required')
    if any(getattr(journal,c.name) is not None for c in JournalEntry.__table__.columns
           if c.name.endswith('_id') and c.name not in {'company_id','id','reversal_of_id','accounting_rule_id'}):
        raise SickReturnError('Return journal belongs to another business document')
    from app.models.payroll_insurance_funding import PayrollInsuranceFunding
    from app.models.payroll_tax_remittance import PayrollTaxRemittance
    from app.models.payroll_advance_tax_remittance import PayrollAdvanceTaxRemittance
    for model in (PayrollSickReturn,PayrollInsuranceFunding,PayrollTaxRemittance,PayrollAdvanceTaxRemittance):
        if await db.scalar(select(model.id).where(model.company_id==company_id,
            model.journal_entry_id==journal_entry_id)) is not None:
            raise SickReturnError('Bank journal already attributed')
    bank = await db.scalar(select(BankAccount).where(BankAccount.company_id==company_id,
        BankAccount.id==bank_account_id, BankAccount.is_active.is_(True)))
    if bank is None or bank.currency_code!='UAH':
        raise SickReturnError('An active UAH bank account is required')
    from app.models.account import Account
    bank_gl = await db.get(Account, bank.accounting_account_id)
    if bank_gl is None or bank_gl.account_type!='asset' or not bank_gl.code.startswith('31'):
        raise SickReturnError('Return requires a bank asset account')
    role = Role.PAYROLL_NET_PAYABLE if kind=='employee' else Role.PAYROLL_INSURANCE_RECEIVABLE
    accounts = await resolve_company_account_roles(db, company_id=company_id, roles=(role,))
    account_id = accounts[role].id
    if account_id==bank.accounting_account_id:
        raise SickReturnError('Bank and settlement accounts must differ')
    debit_id, credit_id = ((bank.accounting_account_id,account_id) if kind=='employee'
                           else (account_id,bank.accounting_account_id))
    debit = credit = D(0)
    for line in journal.lines:
        if line.account_id==debit_id and not line.credit:
            debit += line.debit
        elif line.account_id==credit_id and not line.debit:
            credit += line.credit
        elif line.debit or line.credit:
            raise SickReturnError('Return journal contains unrelated amounts or wrong direction')
    if debit<=0 or debit!=credit:
        raise SickReturnError('Return must reconcile to the full posted bank journal')
    await recognized_sick_chain(db, company_id=company_id, calculation_id=calculation_id,
        as_of=journal.entry_date, error_type=SickReturnError)
    chain = await sick_settlement_chain(db, company_id=company_id, calculation_id=calculation_id)
    later_return = await db.scalar(select(PayrollSickReturn.id).join(JournalEntry,
        (JournalEntry.company_id==PayrollSickReturn.company_id) &
        (JournalEntry.id==PayrollSickReturn.journal_entry_id)).where(
        PayrollSickReturn.company_id==company_id,
        PayrollSickReturn.payroll_calculation_id.in_(chain), PayrollSickReturn.kind==kind,
        JournalEntry.entry_date>journal.entry_date).limit(1))
    if later_return is not None:
        raise SickReturnError('A later return is already registered; reconcile returns in date order')
    summary = await sick_settlement_summary(db, company_id=company_id,
        calculation_id=calculation_id, as_of=journal.entry_date)
    ceiling = summary['employee_overpayment' if kind=='employee' else 'insurer_excess']
    if debit>ceiling:
        raise SickReturnError('Confirmed return exceeds the outstanding overpayment')
    row = PayrollSickReturn(company_id=company_id, payroll_calculation_id=calculation_id,
        journal_entry_id=journal.id, kind=kind, amount=debit, request_key=request_key,
        request_fingerprint=fingerprint, document_reference=document_reference.strip(), created_by=actor_id)
    db.add(row)
    await db.flush()
    return row
