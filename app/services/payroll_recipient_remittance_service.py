"""Attribute actual bank payments to recognized recipient liabilities."""
from decimal import Decimal as D
from sqlalchemy import select, and_, func
from sqlalchemy.orm import aliased, selectinload
from app.models.payroll_deduction_execution import PayrollWithholdingBatch as Batch, PayrollWithholdingLine as Line, PayrollDeductionAssessment as Assessment, PayrollRecipientRemittance as Remittance, PayrollRecipientRemittanceLine as Allocation
from app.models.payroll_deduction_beneficiary import PayrollDeductionBeneficiary as Beneficiary
from app.models.journal_entry import JournalEntry
from app.models.bank_statement_line import BankStatementLine
from app.models.bank_account import BankAccount
from app.models.bank_statement_reconciliation import BankStatementReconciliationActiveLink
from app.services.payroll_deduction_execution_service import DeductionExecutionError, _payment_join
from app.services.payroll_deduction_beneficiary_service import _permission
from app.services.employee_bank_details import normalize_employee_iban
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_insurance_funding_service import peak_commitment
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


async def remitted_commitment(db, company_id, withholding_line_id, from_date):
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(Allocation.amount, JournalEntry.entry_date, reversal.entry_date)
        .join(Remittance, and_(Remittance.company_id == Allocation.company_id, Remittance.id == Allocation.remittance_id))
        .join(JournalEntry, and_(JournalEntry.company_id == Remittance.company_id, JournalEntry.id == Remittance.journal_entry_id))
        .outerjoin(reversal, and_(reversal.company_id == JournalEntry.company_id,
            reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted'))
        .where(Allocation.company_id == company_id, Allocation.withholding_line_id == withholding_line_id,
            JournalEntry.status.in_(('posted','reversed'))))).all()
    return peak_commitment(rows, from_date)


@serialized_payroll_mutation(DeductionExecutionError)
async def register_recipient_remittance(db, *, company_id, data, actor_id):
    await _permission(db, company_id, actor_id)
    fingerprint = generate_request_fingerprint(data.model_dump(mode='json',
        exclude={'payment_beneficiary_id'} if data.payment_beneficiary_id is None else set()))
    old = await db.scalar(select(Remittance).where(Remittance.company_id == company_id, Remittance.request_key == data.request_key))
    if old is not None:
        if old.request_fingerprint != fingerprint:
            raise DeductionExecutionError('Remittance request contains different data')
        return old
    beneficiary = await db.scalar(select(Beneficiary).where(Beneficiary.company_id == company_id,
        Beneficiary.id == data.beneficiary_id).execution_options(populate_existing=True))
    if beneficiary is None or beneficiary.status not in ('verified','revoked') or beneficiary.settlement_kind != 'recipient_payment':
        raise DeductionExecutionError('Verified external recipient required')
    payment_beneficiary = await db.scalar(select(Beneficiary).where(Beneficiary.company_id==company_id,
        Beneficiary.id==(data.payment_beneficiary_id or beneficiary.id)).execution_options(populate_existing=True))
    if payment_beneficiary is None or payment_beneficiary.status!='verified':
        raise DeductionExecutionError('Verified payment recipient details required')
    if any(getattr(payment_beneficiary,key)!=getattr(beneficiary,key) for key in
            ('instruction_id','recipient_identifier','account_id','settlement_kind')):
        raise DeductionExecutionError('New bank details must belong to the same legal recipient and liability')
    payment = await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id == company_id, JournalEntry.id == data.journal_entry_id)
        .with_for_update().execution_options(populate_existing=True))
    if payment is None or payment.status != 'posted' or payment.reversal_of_id is not None:
        raise DeductionExecutionError('Original posted bank journal required')
    if any(getattr(payment,c.name) is not None for c in JournalEntry.__table__.columns
        if c.name.endswith('_id') and c.name not in {'company_id','id','reversal_of_id','accounting_rule_id'}):
        raise DeductionExecutionError('Journal belongs to another business document')
    bank_line = await db.scalar(select(BankStatementLine).where(BankStatementLine.company_id == company_id,
        BankStatementLine.id == data.bank_statement_line_id).with_for_update().execution_options(populate_existing=True))
    amount = sum((x.amount for x in data.allocations), D(0))
    if bank_line is None or bank_line.currency_code != 'UAH' or bank_line.amount != -amount:
        raise DeductionExecutionError('Bank evidence does not match the outgoing amount')
    if bank_line.transaction_date != payment.entry_date or normalize_employee_iban(bank_line.counterparty_account) != payment_beneficiary.iban:
        raise DeductionExecutionError('Bank evidence date or recipient IBAN does not match')
    if not payment_beneficiary.valid_from <= payment.entry_date or (payment_beneficiary.valid_to is not None and payment_beneficiary.valid_to < payment.entry_date):
        raise DeductionExecutionError('Recipient is not valid on payment date')
    existing_link = await db.scalar(select(BankStatementReconciliationActiveLink.id).where(
        BankStatementReconciliationActiveLink.company_id == company_id,
        BankStatementReconciliationActiveLink.bank_statement_line_id == bank_line.id))
    if existing_link is not None:
        raise DeductionExecutionError('Bank transaction is already reconciled to another operation')
    for field, value in ((Remittance.journal_entry_id,payment.id),(Remittance.bank_statement_line_id,bank_line.id)):
        if await db.scalar(select(Remittance.id).where(Remittance.company_id == company_id, field == value)):
            raise DeductionExecutionError('Bank payment is already attributed')
    # A manual journal already assigned to tax/insurance analytics cannot be reused.
    from app.models.payroll_tax_remittance import PayrollTaxRemittance
    from app.models.payroll_insurance_funding import PayrollInsuranceFunding
    for model in (PayrollTaxRemittance, PayrollInsuranceFunding):
        if await db.scalar(select(model.id).where(model.company_id == company_id, model.journal_entry_id == payment.id)):
            raise DeductionExecutionError('Bank journal already belongs to another payroll settlement')
    bank = await db.get(BankAccount, bank_line.bank_account_id)
    if bank is None or bank.company_id != company_id or bank.currency_code != 'UAH':
        raise DeductionExecutionError('Company UAH bank account required')
    expected = [(beneficiary.account_id,amount,D(0)), (bank.accounting_account_id,D(0),amount)]
    if sorted(expected) != sorted((x.account_id,x.debit,x.credit) for x in payment.lines):
        raise DeductionExecutionError('Journal does not settle exactly the recipient liability against bank')
    ids = [x.withholding_line_id for x in data.allocations]
    if len(set(ids)) != len(ids):
        raise DeductionExecutionError('Duplicate withholding allocation')
    for requested in data.allocations:
        source = (await db.execute(select(Line, Assessment.beneficiary_id, JournalEntry).join(Assessment,
            and_(Assessment.company_id == Line.company_id, Assessment.id == Line.assessment_id))
            .join(Batch, and_(Batch.company_id == Line.company_id, Batch.id == Line.batch_id))
            .join(JournalEntry, _payment_join())
            .where(Line.company_id == company_id, Line.id == requested.withholding_line_id))).one_or_none()
        if source is None:
            raise DeductionExecutionError('Recognized withholding not found')
        line, recipient_id, journal = source
        if recipient_id != beneficiary.id or journal.status != 'posted' or journal.entry_date > payment.entry_date:
            raise DeductionExecutionError('Payment must settle an active prior withholding for this recipient')
        used = await remitted_commitment(db, company_id, line.id, payment.entry_date)
        if used + requested.amount > line.amount:
            raise DeductionExecutionError('Remittance exceeds outstanding recipient liability')
    row = Remittance(company_id=company_id, beneficiary_id=beneficiary.id, payment_beneficiary_id=payment_beneficiary.id, journal_entry_id=payment.id,
        bank_statement_line_id=bank_line.id, amount=amount, request_key=data.request_key,
        request_fingerprint=fingerprint, created_by=actor_id)
    db.add(row); await db.flush()
    db.add_all([Allocation(company_id=company_id, remittance_id=row.id,
        withholding_line_id=x.withholding_line_id, amount=x.amount) for x in data.allocations])
    await db.flush()
    return row


async def ensure_withholding_reversible(db, *, company_id, journal, reversal_date, error_type):
    batches = list((await db.scalars(select(Batch.id).where(Batch.company_id == company_id,
        ((Batch.advance_id == journal.payroll_advance_id) if journal.payroll_advance_id is not None else
         (Batch.disbursement_id == journal.payroll_disbursement_id))))) .all()) if (journal.payroll_advance_id or journal.payroll_disbursement_id) else []
    for line_id in (await db.scalars(select(Line.id).where(Line.company_id == company_id, Line.batch_id.in_(batches)))).all() if batches else []:
        if await remitted_commitment(db, company_id, line_id, reversal_date) > 0:
            raise error_type('Reverse recipient remittances before reversing the salary withholding')


@serialized_payroll_mutation(DeductionExecutionError)
async def reverse_recipient_remittance(db, *, company_id, remittance_id, reversal_date, actor_id):
    await _permission(db, company_id, actor_id)
    row = await db.scalar(select(Remittance).where(Remittance.company_id == company_id, Remittance.id == remittance_id))
    if row is None:
        raise DeductionExecutionError('Recipient remittance not found')
    from app.services.accounting_reversal import reverse_journal_entry
    await reverse_journal_entry(db, company_id, row.journal_entry_id, reversal_date, actor_id)
    return row
