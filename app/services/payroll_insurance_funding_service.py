"""No duplicate posting: attach actual bank receipt to posted insurer debt."""
from decimal import Decimal as D
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, aliased
from app.models.account import Account
from app.models.bank_account import BankAccount
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_insurance_funding import PayrollInsuranceFunding as Funding, PayrollInsuranceFundingLine as Line
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.accounting_account_role_resolver import resolve_company_account_roles
from app.services.payroll_sick_accounting_service import sick_funding_for_calculation
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class InsuranceFundingError(ValueError):
    pass


class InsuranceFundingNotFoundError(InsuranceFundingError):
    pass


async def funding_record(db, *, company_id, funding_id):
    row = await db.scalar(select(Funding).where(Funding.company_id==company_id,Funding.id==funding_id))
    if row is None:
        raise InsuranceFundingNotFoundError('Insurance funding not found')
    return row


async def funding_response(db, row):
    journal=await db.get(JournalEntry,row.journal_entry_id)
    lines=(await db.scalars(select(Line).where(Line.company_id==row.company_id,Line.funding_id==row.id)
        .order_by(Line.payroll_calculation_id))).all()
    return dict(id=row.id,company_id=row.company_id,journal_entry_id=row.journal_entry_id,
        bank_account_id=row.bank_account_id,amount=row.amount,document_reference=row.document_reference,
        separate_account_reference=row.separate_account_reference,created_by=row.created_by,
        created_at=row.created_at,receipt_date=journal.entry_date,status=journal.status,
        allocations=[dict(payroll_calculation_id=x.payroll_calculation_id,amount=x.amount) for x in lines])


@serialized_payroll_mutation(InsuranceFundingError)
async def register_insurance_funding(db, *, company_id, data, created_by):
    payload=data.model_dump(mode='json')
    payload['allocations']=sorted(payload['allocations'],key=lambda x:x['payroll_calculation_id'])
    fingerprint=generate_request_fingerprint(payload)
    old=await db.scalar(select(Funding).where(Funding.company_id==company_id,Funding.request_key==data.request_key))
    if old:
        if old.request_fingerprint!=fingerprint:
            raise InsuranceFundingError('Request key contains different insurance funding')
        return old
    if await db.scalar(select(Funding.id).where(Funding.company_id==company_id,Funding.journal_entry_id==data.journal_entry_id)):
        raise InsuranceFundingError('Receipt journal already attributed')
    receipt=await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
        JournalEntry.company_id==company_id,JournalEntry.id==data.journal_entry_id)
        .with_for_update().execution_options(populate_existing=True))
    from app.models.payroll_sick_return import PayrollSickReturn
    if await db.scalar(select(PayrollSickReturn.id).where(
        PayrollSickReturn.company_id==company_id,
        PayrollSickReturn.journal_entry_id==data.journal_entry_id)) is not None:
        raise InsuranceFundingError('Bank journal already attributed to a sick correction return')
    if receipt is None or receipt.status!='posted' or receipt.reversal_of_id is not None:
        raise InsuranceFundingError('An original posted bank receipt is required')
    if any(getattr(receipt,c.name) is not None for c in JournalEntry.__table__.columns
           if c.name.endswith('_id') and c.name not in {'company_id','id','reversal_of_id','accounting_rule_id'}):
        raise InsuranceFundingError('Receipt is already owned by another business document')
    bank=await db.scalar(select(BankAccount).where(BankAccount.company_id==company_id,
        BankAccount.id==data.bank_account_id,BankAccount.is_active.is_(True)))
    if bank is None or bank.currency_code!='UAH':
        raise InsuranceFundingError('An active UAH insurance bank account is required')
    account=await db.get(Account,bank.accounting_account_id)
    if account.account_type!='asset' or not account.code.startswith('31'):
        raise InsuranceFundingError('Insurance funding requires a bank asset account')
    resolved=await resolve_company_account_roles(db,company_id=company_id,roles=(Role.PAYROLL_INSURANCE_RECEIVABLE,))
    receivable=resolved[Role.PAYROLL_INSURANCE_RECEIVABLE].id
    amount=sum((line.amount for line in data.allocations),D(0))
    if bank.accounting_account_id==receivable:
        raise InsuranceFundingError('Bank and insurance receivable accounts must be different')
    debit=credit=D(0)
    for line in receipt.lines:
        if line.account_id==bank.accounting_account_id and line.credit==0:
            debit+=line.debit
        elif line.account_id==receivable and line.debit==0:
            credit+=line.credit
        elif line.debit or line.credit:
            raise InsuranceFundingError('Receipt contains unrelated accounting lines')
    if debit!=amount or credit!=amount:
        raise InsuranceFundingError('Funding allocations must reconcile to the full bank receipt')
    for requested in data.allocations:
        calculation=await db.scalar(select(PayrollCalculation).where(PayrollCalculation.company_id==company_id,
            PayrollCalculation.id==requested.payroll_calculation_id))
        if calculation is None:
            raise InsuranceFundingNotFoundError('Payroll calculation not found in company')
        period=await db.get(PayrollPeriod,calculation.payroll_period_id)
        from app.services.payroll_revision_service import require_current_calculation
        from app.services.payroll_sick_financial_sources import recognized_sick_chain
        await require_current_calculation(db, calculation=calculation, error_type=InsuranceFundingError)
        accruals = await recognized_sick_chain(db, company_id=company_id, calculation_id=calculation.id,
            as_of=receipt.entry_date, error_type=InsuranceFundingError)
        try:
            _, entitled=await sick_funding_for_calculation(db,company_id=company_id,calculation=calculation,period=period)
        except ValueError as exc:
            raise InsuranceFundingError(str(exc)) from exc
        recognized=sum((x.debit-x.credit for accrual in accruals for x in accrual.lines if x.account_id==receivable),D(0))
        if recognized!=entitled:
            raise InsuranceFundingError('Posted insurer receivable differs from payroll source')
        used=await funding_commitment(db,company_id=company_id,calculation_id=calculation.id,
            from_date=receipt.entry_date)
        if D(used)+requested.amount>entitled:
            raise InsuranceFundingError('Insurance funding exceeds the unpaid recognized receivable')
    row=Funding(company_id=company_id,journal_entry_id=receipt.id,bank_account_id=bank.id,
        amount=amount,request_key=data.request_key,request_fingerprint=fingerprint,
        document_reference=data.document_reference,separate_account_reference=data.separate_account_reference,
        created_by=created_by)
    db.add(row);await db.flush()
    db.add_all([Line(company_id=company_id,funding_id=row.id,payroll_calculation_id=x.payroll_calculation_id,
        amount=x.amount) for x in data.allocations])
    await db.flush()
    return row


def peak_commitment(rows, from_date):
    """Largest outstanding amount from a date onward, including later receipts."""
    changes={}
    for amount,received,reversed_on in rows:
        changes[received]=changes.get(received,D(0))+D(amount)
        if reversed_on is not None:
            if reversed_on<received:
                raise InsuranceFundingError('Funding reversal precedes its receipt')
            changes[reversed_on]=changes.get(reversed_on,D(0))-D(amount)
    balance=sum((amount for day,amount in changes.items() if day<=from_date),D(0))
    peak=balance
    for day in sorted(day for day in changes if day>from_date):
        balance+=changes[day]
        peak=max(peak,balance)
    return peak


async def funding_commitment(db, *, company_id, calculation_id, from_date):
    from app.services.payroll_sick_financial_sources import sick_settlement_chain
    calculation_ids = await sick_settlement_chain(db, company_id=company_id, calculation_id=calculation_id)
    reversal=aliased(JournalEntry)
    rows=(await db.execute(select(Line.amount,JournalEntry.entry_date,reversal.entry_date)
        .join(Funding,(Funding.company_id==Line.company_id)&(Funding.id==Line.funding_id))
        .join(JournalEntry,(JournalEntry.company_id==Funding.company_id)&(JournalEntry.id==Funding.journal_entry_id))
        .outerjoin(reversal,(reversal.company_id==JournalEntry.company_id)&(reversal.reversal_of_id==JournalEntry.id)&(reversal.status=='posted'))
        .where(Line.company_id==company_id,Line.payroll_calculation_id.in_(calculation_ids),
               JournalEntry.status.in_(('posted','reversed'))))).all()
    from app.services.payroll_sick_settlement_service import available_return_credit
    returned = await available_return_credit(db, company_id=company_id,
        calculation_ids=calculation_ids, kind='insurer', from_date=from_date)
    return peak_commitment(rows,from_date)-returned


async def ensure_no_insurance_funding(db, *, company_id, calculation_id, reversal_date, error_type):
    if await funding_commitment(db,company_id=company_id,calculation_id=calculation_id,from_date=reversal_date)>0:
        raise error_type('Reverse attributed insurance funding before reversing payroll accrual')


@serialized_payroll_mutation(InsuranceFundingError)
async def reverse_insurance_funding(db, *, company_id, funding_id, reversal_date, reversed_by):
    from app.services.accounting_reversal import reverse_journal_entry
    row=await funding_record(db,company_id=company_id,funding_id=funding_id)
    await reverse_journal_entry(db,company_id,row.journal_entry_id,reversal_date,reversed_by)
    return row


async def ensure_funding_receipt_reversible(db, *, company_id, journal_entry_id, reversal_date, error_type):
    from app.models.payroll_disbursement import PayrollDisbursement
    funding=await db.scalar(select(Funding.id).where(Funding.company_id==company_id,Funding.journal_entry_id==journal_entry_id))
    if funding is None:
        return
    ids=select(Line.payroll_calculation_id).where(Line.company_id==company_id,Line.funding_id==funding)
    reversal=aliased(JournalEntry)
    payments=(await db.execute(select(PayrollDisbursement.amount,JournalEntry.entry_date,reversal.entry_date)
        .join(JournalEntry,(PayrollDisbursement.company_id==JournalEntry.company_id)&(PayrollDisbursement.id==JournalEntry.payroll_disbursement_id))
        .outerjoin(reversal,(reversal.company_id==JournalEntry.company_id)&(reversal.reversal_of_id==JournalEntry.id)&(reversal.status=='posted'))
        .where(JournalEntry.company_id==company_id,JournalEntry.status.in_(('posted','reversed')),JournalEntry.reversal_of_id.is_(None),
               PayrollDisbursement.payroll_calculation_id.in_(ids)))).all()
    if peak_commitment(payments,reversal_date)>0:
        raise error_type('Reverse dependent payroll payouts before reversing insurance funding')
