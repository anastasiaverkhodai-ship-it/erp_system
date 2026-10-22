"""Execute accountant-confirmed claims against each net salary payment.

Assessment amounts are NEW amounts due. Unapplied previous claims carry forward
without copying their balance into another assessment. No technical D1-D4 object
is accepted as financial authority.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal as D, ROUND_DOWN
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import aliased, selectinload
from app.models.payroll_deduction_execution import (
    PayrollDeductionAssessment as Assessment, PayrollWithholdingBatch as Batch,
    PayrollWithholdingLine as Line, PayrollRecipientRemittance as Remittance,
    PayrollRecipientRemittanceLine as RemittanceLine,
    PayrollDeductionAssessmentClosure as Closure,
)
from app.models.payroll_deduction import PayrollDeductionInstruction as Instruction
from app.models.payroll_deduction_rule_approval import PayrollDeductionRuleApproval as Approval
from app.models.payroll_deduction_beneficiary import PayrollDeductionBeneficiary as Beneficiary
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_disbursement import PayrollDisbursement
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_deduction_beneficiary_service import _permission, resolve_verified_beneficiary
from app.services.payroll_deduction_evidence_policy import resolve_verified_deduction_classification
from app.services.payroll_deduction_trusted_approval_service import resolve_trusted_rule_approval
from app.services.payroll_deduction_payment_allocator import (
    PaymentClaim, PaymentGroupLimit, allocate_grouped_payment_deductions,
)
from app.services.idempotency_fingerprint_service import generate_request_fingerprint

_PREPARING = object()


class DeductionExecutionError(ValueError):
    pass


def preparing(db):
    return db.info.get('payroll_withholding_prepare') is _PREPARING


@contextmanager
def _prepare(db):
    previous = db.info.get('payroll_withholding_prepare')
    db.info['payroll_withholding_prepare'] = _PREPARING
    try:
        yield
    finally:
        if previous is None:
            db.info.pop('payroll_withholding_prepare', None)
        else:
            db.info['payroll_withholding_prepare'] = previous


async def active_instructions(db, company_id, employee_id, on):
    return list((await db.scalars(select(Instruction).join(EmploymentContract,
        and_(EmploymentContract.company_id == Instruction.company_id,
             EmploymentContract.id == Instruction.employment_contract_id)).where(
        Instruction.company_id == company_id, EmploymentContract.employee_id == employee_id,
        Instruction.effective_from <= on, or_(Instruction.effective_to.is_(None), Instruction.effective_to >= on))
        .execution_options(populate_existing=True))).all())


async def _assessment_sources(db, row, on):
    # Keep the source state stable until the payment transaction commits.
    from app.models.payroll_deduction_legal_evidence import PayrollDeductionLegalEvidence
    await db.scalar(select(PayrollDeductionLegalEvidence).where(
        PayrollDeductionLegalEvidence.company_id == row.company_id,
        PayrollDeductionLegalEvidence.payroll_deduction_instruction_id == row.instruction_id)
        .with_for_update().execution_options(populate_existing=True))
    recipient = await resolve_verified_beneficiary(db, company_id=row.company_id,
        instruction_id=row.instruction_id, payment_date=on)
    if recipient.id != row.beneficiary_id:
        raise DeductionExecutionError('Assessment recipient is no longer the verified version')
    classification = await resolve_verified_deduction_classification(db, company_id=row.company_id,
        payroll_deduction_instruction_id=row.instruction_id)
    approval = await db.scalar(select(Approval).where(Approval.company_id == row.company_id,
        Approval.id == row.rule_approval_id).with_for_update().execution_options(populate_existing=True))
    if approval is None or approval.legal_category != classification.category:
        raise DeductionExecutionError('Assessment rule approval does not match its legal document')
    await resolve_trusted_rule_approval(db, company_id=row.company_id, legal_category=classification.category,
        rule_version=approval.rule_version, effective_on=on)
    if row.individual_limit_percentage > classification.individual_limit_percentage:
        raise DeductionExecutionError('Documented individual ceiling exceeds the statutory ceiling')
    return classification.category, recipient


@serialized_payroll_mutation(DeductionExecutionError)
async def create_assessment(db, *, company_id, data, actor_id):
    await _permission(db, company_id, actor_id)
    old = await db.scalar(select(Assessment).where(Assessment.company_id == company_id,
        Assessment.request_key == data.request_key))
    if old is not None:
        if any(getattr(old, k) != v for k,v in data.model_dump().items()):
            raise DeductionExecutionError('Assessment request contains different data')
        return old
    row = Assessment(company_id=company_id, created_by=actor_id, **data.model_dump())
    await _assessment_sources(db, row, row.effective_from)
    # A new amount must not silently rewrite the factual basis of an older payment.
    last_payment = await db.scalar(select(func.max(Batch.payment_date)).join(Line,
        and_(Line.company_id == Batch.company_id, Line.batch_id == Batch.id)).join(Assessment,
        and_(Assessment.company_id == Line.company_id, Assessment.id == Line.assessment_id))
        .where(Batch.company_id == company_id, Assessment.instruction_id == row.instruction_id))
    if last_payment is not None and row.effective_from < last_payment:
        raise DeductionExecutionError('Cannot backdate an assessment before an existing payment')
    duplicate = await db.scalar(select(Assessment.id).where(Assessment.company_id == company_id,
        Assessment.instruction_id == row.instruction_id, Assessment.reference == row.reference))
    if duplicate is not None:
        raise DeductionExecutionError('This assessment document has already been recorded')
    db.add(row); await db.flush()
    return row


def _payment_join():
    return and_(JournalEntry.company_id == Batch.company_id, JournalEntry.reversal_of_id.is_(None),
        or_(and_(Batch.advance_id.is_not(None), JournalEntry.payroll_advance_id == Batch.advance_id),
            and_(Batch.disbursement_id.is_not(None), JournalEntry.payroll_disbursement_id == Batch.disbursement_id)))


async def assessment_used(db, company_id, assessment_id, on):
    reversal = aliased(JournalEntry)
    rows = (await db.execute(select(Line.amount, JournalEntry.entry_date, reversal.entry_date)
        .join(Batch, and_(Batch.company_id == Line.company_id, Batch.id == Line.batch_id))
        .join(JournalEntry, _payment_join())
        .outerjoin(reversal, and_(reversal.company_id == JournalEntry.company_id,
            reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted'))
        .where(Line.company_id == company_id, Line.assessment_id == assessment_id,
               JournalEntry.status.in_(('posted','reversed'))))).all()
    # No backdating across already executed payments: future reservations cannot
    # be spent twice, nor can a later reversal release today's capacity.
    from app.services.payroll_insurance_funding_service import peak_commitment
    return peak_commitment(rows, on)


async def _allocate(db, company_id, employee_id, on, net, protected):
    if not D(0) <= protected <= net:
        raise DeductionExecutionError('Protected net income must be between zero and net payment')
    instructions = await active_instructions(db, company_id, employee_id, on)
    if not instructions:
        raise DeductionExecutionError('No effective deduction instruction; use the ordinary payment lifecycle')
    future_closure = await db.scalar(select(Closure.id).join(Assessment,
        and_(Assessment.company_id==Closure.company_id,Assessment.id==Closure.assessment_id))
        .where(Closure.company_id==company_id,Closure.effective_from>on,
            Assessment.instruction_id.in_([x.id for x in instructions])).limit(1))
    if future_closure is not None:
        raise DeductionExecutionError('Cannot backdate a payment before a recorded assessment correction')
    assessments = list((await db.scalars(select(Assessment).where(Assessment.company_id == company_id,
        Assessment.instruction_id.in_([x.id for x in instructions]), Assessment.effective_from <= on)
        .order_by(Assessment.effective_from, Assessment.id))).all())
    if {x.instruction_id for x in assessments} != {x.id for x in instructions}:
        raise DeductionExecutionError('Every effective instruction requires a confirmed assessment')
    closed_ids = set((await db.scalars(select(Closure.assessment_id).where(
        Closure.company_id == company_id, Closure.effective_from <= on))).all())
    assessments = [row for row in assessments if row.id not in closed_ids]
    eligible = net - protected
    claims, categories, recipients = [], {}, {}
    per_instruction = {}
    for row in assessments:
        used = await assessment_used(db, company_id, row.id, on)
        remaining = max(D(0), row.amount - used)
        # Exhausted claims remain explicit but do not force reuse of old bank details.
        if remaining == 0:
            continue
        category, recipient = await _assessment_sources(db, row, on)
        categories[row.id], recipients[row.id] = category, recipient
        cap = (eligible * row.individual_limit_percentage / 100).quantize(D('.01'), rounding=ROUND_DOWN)
        claims.append(PaymentClaim(row.id, row.priority, remaining, cap))
        per_instruction.setdefault(row.instruction_id, []).append((row.id,cap))
    groups = []
    for values in per_instruction.values():
        groups.append(PaymentGroupLimit(frozenset(i for i,_ in values), min(cap for _,cap in values)))
    for category, percentage in [('employer_statutory',20), ('non_minor',50)]:
        ids = frozenset(i for i,c in categories.items() if c == category or
                        (category == 'non_minor' and c != 'minor_child_alimony'))
        if ids:
            groups.append(PaymentGroupLimit(ids, (eligible * percentage / 100).quantize(D('.01'), rounding=ROUND_DOWN)))
    aggregate_pct = 70 if 'minor_child_alimony' in categories.values() else (50 if any(c != 'employer_statutory' for c in categories.values()) else 20)
    allocations = allocate_grouped_payment_deductions(eligible_net=eligible,
        aggregate_limit=(eligible * aggregate_pct / 100).quantize(D('.01'), rounding=ROUND_DOWN),
        claims=tuple(claims), group_limits=tuple(groups))
    return allocations, recipients


@serialized_payroll_mutation(DeductionExecutionError)
async def execute_payment(db, *, company_id, data, actor_id):
    await _permission(db, company_id, actor_id)
    fingerprint = generate_request_fingerprint(data.model_dump(mode='json'))
    old = await db.scalar(select(Batch).where(Batch.company_id == company_id, Batch.request_key == data.request_key))
    if old is not None:
        if old.request_fingerprint != fingerprint:
            raise DeductionExecutionError('Payment request contains different data')
        journal = await db.scalar(select(JournalEntry).join(Batch, _payment_join()).where(Batch.id == old.id,
            Batch.company_id == company_id))
        if journal is None or journal.status != 'posted':
            raise DeductionExecutionError('Existing withholding payment is not active')
        return old
    async with db.begin_nested():
        with _prepare(db):
            if data.kind != 'opening' and data.opening_debt_id is not None:
                raise DeductionExecutionError('Opening debt is only valid for an opening payment')
            if data.kind == 'advance':
                if data.payroll_period_id is None or data.employment_contract_id is None or data.advance_percentage is None or data.amount is not None or data.payroll_calculation_id is not None:
                    raise DeductionExecutionError('Advance requires period, contract and percentage only')
                from app.services.payroll_automatic_advance_service import create_automatic_payroll_advance
                from app.services.payroll_advance_service import generate_and_post_payroll_advance_journal
                payment = await create_automatic_payroll_advance(db, company_id=company_id,
                    payroll_period_id=data.payroll_period_id, employment_contract_id=data.employment_contract_id,
                    bank_account_id=data.bank_account_id, advance_percentage=data.advance_percentage,
                    payment_date=data.payment_date, created_by=actor_id)
                contract = await db.get(EmploymentContract, payment.employment_contract_id)
                employee_id, net = contract.employee_id, payment.paid_amount
                source = dict(advance_id=payment.id)
                post = generate_and_post_payroll_advance_journal
                post_args = dict(payroll_advance_id=payment.id)
            elif data.kind == 'opening':
                if data.opening_debt_id is None or any(value is not None for value in
                        (data.payroll_period_id,data.employment_contract_id,data.payroll_calculation_id,data.advance_percentage)):
                    raise DeductionExecutionError('Opening payment requires an opening debt only')
                from app.schemas.payroll_disbursement import PayrollDisbursementCreate
                from app.services.payroll_opening_disbursement_service import create_opening_disbursement
                from app.services.payroll_disbursement_service import generate_and_post_payroll_disbursement_journal
                payment = await create_opening_disbursement(db,company_id=company_id,
                    opening_debt_id=data.opening_debt_id,created_by=actor_id,
                    data=PayrollDisbursementCreate(bank_account_id=data.bank_account_id,payment_date=data.payment_date,
                        amount=data.amount,request_key='withholding:'+data.request_key))
                employee_id,net = payment.employee_id,payment.amount
                source = dict(disbursement_id=payment.id)
                post = generate_and_post_payroll_disbursement_journal
                post_args = dict(payroll_disbursement_id=payment.id)
            else:
                if data.payroll_calculation_id is None or data.payroll_period_id is not None or data.employment_contract_id is not None or data.advance_percentage is not None:
                    raise DeductionExecutionError('Salary payment requires a calculation only')
                from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal
                payment = await create_payroll_disbursement(db, company_id=company_id,
                    payroll_calculation_id=data.payroll_calculation_id, bank_account_id=data.bank_account_id,
                    payment_date=data.payment_date, amount=data.amount, request_key='withholding:'+data.request_key,
                    created_by=actor_id)
                employee_id, net = payment.employee_id, payment.amount
                source = dict(disbursement_id=payment.id)
                post = generate_and_post_payroll_disbursement_journal
                post_args = dict(payroll_disbursement_id=payment.id)
        existing = await db.scalar(select(JournalEntry.id).where(JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id.is_(None),
            (JournalEntry.payroll_advance_id == payment.id) if data.kind == 'advance' else (JournalEntry.payroll_disbursement_id == payment.id)))
        existing_batch = await db.scalar(select(Batch.id).where(Batch.company_id == company_id,
            (Batch.advance_id == payment.id) if data.kind == 'advance' else (Batch.disbursement_id == payment.id)))
        if existing is not None or existing_batch is not None or payment.withheld_amount:
            raise DeductionExecutionError('Payment already has an accounting lifecycle')
        if payment.currency_code != 'UAH':
            raise DeductionExecutionError('Operational deductions support UAH only')
        protected = data.protected_amount
        if data.kind == 'salary':
            from app.services.payroll_severance_payment_service import payment_protected_minimum
            try:
                protected = max(protected, await payment_protected_minimum(db, company_id=company_id, payment=payment))
            except ValueError as exc:
                raise DeductionExecutionError(str(exc)) from exc
        allocations, _ = await _allocate(db, company_id, employee_id, data.payment_date, net, protected)
        total = sum((x.withheld_amount for x in allocations), D(0))
        batch = Batch(company_id=company_id, employee_id=employee_id, payment_date=data.payment_date,
            net_before=net, protected_amount=protected, withheld_amount=total,
            income_reference=data.income_reference, request_key=data.request_key, request_fingerprint=fingerprint,
            created_by=actor_id, **source)
        db.add(batch); await db.flush()
        db.add_all([Line(company_id=company_id, batch_id=batch.id, assessment_id=x.instruction_id,
            requested_amount=x.requested_amount, amount=x.withheld_amount) for x in allocations])
        payment.withheld_amount = total
        if data.kind == 'advance': payment.paid_amount = net - total
        else: payment.amount = net - total
        await db.flush()
        await post(db, company_id=company_id, created_by=actor_id, **post_args)
        return batch


async def payment_batch(db, company_id, advance_id=None, disbursement_id=None):
    if advance_id is None and disbursement_id is None:
        return None
    return await db.scalar(select(Batch).where(Batch.company_id == company_id,
        Batch.advance_id == advance_id if advance_id is not None else Batch.disbursement_id == disbursement_id))


async def journal_withholding_lines(db, company_id, *, advance_id=None, disbursement_id=None):
    batch = await payment_batch(db, company_id, advance_id, disbursement_id)
    if batch is None:
        return []
    rows = (await db.execute(select(Line.amount, Beneficiary.account_id).join(Assessment,
        and_(Assessment.company_id == Line.company_id, Assessment.id == Line.assessment_id)).join(Beneficiary,
        and_(Beneficiary.company_id == Assessment.company_id, Beneficiary.id == Assessment.beneficiary_id))
        .where(Line.company_id == company_id, Line.batch_id == batch.id))).all()
    accounts = {}
    for amount, account_id in rows:
        accounts[account_id] = accounts.get(account_id,D(0)) + amount
    if sum(accounts.values(), D(0)) != batch.withheld_amount:
        raise DeductionExecutionError('Withholding lines do not reconcile')
    return [JournalEntryLine(line_no=i+3, account_id=aid, debit=D(0), credit=amount,
        description='Payroll deduction settlement') for i,(aid,amount) in enumerate(sorted(accounts.items())) if amount]


async def validate_payment_batch(db, *, company_id, employee_id, payment_date, advance_id=None, disbursement_id=None):
    batch = await payment_batch(db, company_id, advance_id, disbursement_id)
    if batch is None:
        return False
    if batch.employee_id != employee_id or batch.payment_date != payment_date:
        raise DeductionExecutionError('Withholding payment identity mismatch')
    model = PayrollAdvance if advance_id is not None else PayrollDisbursement
    payment = await db.scalar(select(model).where(model.company_id == company_id,
        model.id == (advance_id if advance_id is not None else disbursement_id)).execution_options(populate_existing=True))
    paid = payment.paid_amount if advance_id is not None else payment.amount
    if payment.withheld_amount != batch.withheld_amount or paid + batch.withheld_amount != batch.net_before:
        raise DeductionExecutionError('Withholding payment amounts do not reconcile')
    if disbursement_id is not None:
        from app.services.payroll_severance_payment_service import payment_protected_minimum
        try:
            minimum = await payment_protected_minimum(db, company_id=company_id, payment=payment)
        except ValueError as exc:
            raise DeductionExecutionError(str(exc)) from exc
        if batch.protected_amount < minimum:
            raise DeductionExecutionError('Withholding would consume protected severance')
    expected, _ = await _allocate(db, company_id, employee_id, payment_date, batch.net_before, batch.protected_amount)
    lines = (await db.scalars(select(Line).where(Line.company_id == company_id, Line.batch_id == batch.id))).all()
    if sorted((x.assessment_id,x.requested_amount,x.amount) for x in lines) != sorted(
        (x.instruction_id,x.requested_amount,x.withheld_amount) for x in expected):
        raise DeductionExecutionError('Withholding payment no longer matches its assessment')
    return True


async def validate_withholding_journal(db, journal, error_type):
    if journal.reversal_of_id is not None:
        return
    batch = await payment_batch(db, journal.company_id, journal.payroll_advance_id, journal.payroll_disbursement_id)
    if batch is None:
        model = PayrollAdvance if journal.payroll_advance_id is not None else PayrollDisbursement
        pid = journal.payroll_advance_id or journal.payroll_disbursement_id
        if pid is not None:
            amount = await db.scalar(select(model.withheld_amount).where(model.company_id == journal.company_id, model.id == pid))
            if amount:
                raise error_type('Withheld amount requires its operational ledger')
        return
    from app.models.bank_account import BankAccount
    from app.services.accounting_account_roles import AccountingAccountRole as Role
    from app.services.accounting_account_role_resolver import resolve_company_account_roles
    payment = await db.get(PayrollAdvance if batch.advance_id else PayrollDisbursement, batch.advance_id or batch.disbursement_id)
    bank = await db.get(BankAccount, payment.bank_account_id)
    roles = await resolve_company_account_roles(db, company_id=journal.company_id, roles=(Role.PAYROLL_NET_PAYABLE,))
    expected = [(roles[Role.PAYROLL_NET_PAYABLE].id, batch.net_before, D(0)),
                (bank.accounting_account_id, D(0), batch.net_before - batch.withheld_amount)]
    expected += [(x.account_id,x.debit,x.credit) for x in await journal_withholding_lines(db, journal.company_id,
        advance_id=batch.advance_id, disbursement_id=batch.disbursement_id)]
    if journal.entry_date != batch.payment_date or sorted(expected) != sorted((x.account_id,x.debit,x.credit) for x in journal.lines):
        raise error_type('Payroll withholding journal does not match the operational ledger')


async def withholding_rows(db, company_id, *, employee_id=None, period_id=None, contract_id=None, as_of=date.max, active_only=True):
    reversal = aliased(JournalEntry)
    query = select(Batch, Line, Assessment, JournalEntry, reversal.entry_date).join(Line,
        and_(Line.company_id == Batch.company_id, Line.batch_id == Batch.id)).join(Assessment,
        and_(Assessment.company_id == Line.company_id, Assessment.id == Line.assessment_id)).join(JournalEntry, _payment_join())
    query = query.outerjoin(reversal, and_(reversal.company_id == JournalEntry.company_id,
        reversal.reversal_of_id == JournalEntry.id, reversal.status == 'posted')).where(
        Batch.company_id == company_id, JournalEntry.status.in_(('posted','reversed')), JournalEntry.entry_date <= as_of,
        or_(reversal.id.is_(None), reversal.entry_date > as_of) if active_only else True)
    if employee_id is not None: query = query.where(Batch.employee_id == employee_id)
    if period_id is not None or contract_id is not None:
        from app.models.payroll import PayrollCalculation
        query = query.outerjoin(PayrollAdvance, and_(PayrollAdvance.company_id == Batch.company_id, PayrollAdvance.id == Batch.advance_id))
        query = query.outerjoin(PayrollDisbursement, and_(PayrollDisbursement.company_id == Batch.company_id, PayrollDisbursement.id == Batch.disbursement_id))
        query = query.outerjoin(PayrollCalculation, and_(PayrollCalculation.company_id == Batch.company_id,
            PayrollCalculation.id == PayrollDisbursement.payroll_calculation_id))
        if period_id is not None:
            query = query.where(or_(PayrollAdvance.payroll_period_id == period_id, PayrollCalculation.payroll_period_id == period_id))
        if contract_id is not None:
            query = query.where(or_(PayrollAdvance.employment_contract_id == contract_id, PayrollCalculation.employment_contract_id == contract_id))
    return (await db.execute(query)).all()


async def recipient_balances(db, *, company_id, as_of):
    from app.models.company import Company
    if await db.scalar(select(Company.id).where(Company.id == company_id).with_for_update(read=True)) is None:
        raise DeductionExecutionError('Company not found')
    balances = {}
    for batch, line, assessment, journal, reversal in await withholding_rows(db, company_id, as_of=as_of):
        item = balances.setdefault(assessment.beneficiary_id, dict(beneficiary_id=assessment.beneficiary_id,
            withheld_amount=D(0), remitted_amount=D(0), balance=D(0)))
        item['withheld_amount'] += line.amount
    reverse = aliased(JournalEntry)
    rows = (await db.execute(select(Remittance.beneficiary_id, Remittance.amount).join(JournalEntry,
        and_(JournalEntry.company_id == Remittance.company_id, JournalEntry.id == Remittance.journal_entry_id))
        .outerjoin(reverse, and_(reverse.company_id == JournalEntry.company_id, reverse.reversal_of_id == JournalEntry.id,
            reverse.status == 'posted')).where(Remittance.company_id == company_id,
        JournalEntry.status.in_(('posted','reversed')), JournalEntry.entry_date <= as_of,
        or_(reverse.id.is_(None), reverse.entry_date > as_of)))).all()
    for beneficiary_id, amount in rows:
        item = balances.setdefault(beneficiary_id, dict(beneficiary_id=beneficiary_id,withheld_amount=D(0),remitted_amount=D(0),balance=D(0)))
        item['remitted_amount'] += amount
    for item in balances.values():
        recipient = await db.get(Beneficiary,item['beneficiary_id'])
        item['settlement_kind'] = recipient.settlement_kind
        item['recipient_name'] = recipient.recipient_name
        item['account_id'] = recipient.account_id
        item['balance'] = item['withheld_amount']-item['remitted_amount'] if recipient.settlement_kind == 'recipient_payment' else D(0)
    return dict(company_id=company_id,as_of=as_of,currency_code='UAH',recipients=list(balances.values()))


@serialized_payroll_mutation(DeductionExecutionError)
async def correct_assessment(db, *, company_id, assessment_id, data, actor_id):
    """Close future unused capacity, optionally with a confirmed replacement."""
    await _permission(db,company_id,actor_id)
    fingerprint=generate_request_fingerprint(dict(assessment_id=assessment_id,**data.model_dump(mode='json')))
    old=await db.scalar(select(Closure).where(Closure.company_id==company_id,Closure.request_key==data.request_key))
    if old is not None:
        if old.request_fingerprint != fingerprint:
            raise DeductionExecutionError('Correction request contains different data')
        return old
    source=await db.scalar(select(Assessment).where(Assessment.company_id==company_id,Assessment.id==assessment_id))
    if source is None:
        raise DeductionExecutionError('Assessment not found in company')
    if await db.scalar(select(Closure.id).where(Closure.company_id==company_id,Closure.assessment_id==source.id)):
        raise DeductionExecutionError('Assessment already corrected; correct its replacement instead')
    if data.effective_from < source.effective_from:
        raise DeductionExecutionError('Correction cannot predate its assessment')
    last_payment=await db.scalar(select(func.max(Batch.payment_date)).join(Line,
        and_(Line.company_id==Batch.company_id,Line.batch_id==Batch.id))
        .where(Line.company_id==company_id,Line.assessment_id==source.id))
    if last_payment is not None and data.effective_from <= last_payment:
        raise DeductionExecutionError('Correction must follow the last recorded payment date')
    replacement=data.replacement
    if replacement is not None and (replacement.instruction_id!=source.instruction_id or replacement.effective_from!=data.effective_from):
        raise DeductionExecutionError('Replacement must keep the instruction and correction date')
    if replacement is not None and await db.scalar(select(Assessment.id).where(
            Assessment.company_id==company_id,Assessment.request_key==replacement.request_key)):
        raise DeductionExecutionError('Replacement must be a new confirmed assessment')
    async with db.begin_nested():
        new=await create_assessment(db,company_id=company_id,data=replacement,actor_id=actor_id) if replacement is not None else None
        if new is not None and (new.id==source.id or await db.scalar(select(Line.id).where(Line.company_id==company_id,Line.assessment_id==new.id).limit(1))):
            raise DeductionExecutionError('Replacement cannot reuse an already executed assessment')
        row=Closure(company_id=company_id,assessment_id=source.id,effective_from=data.effective_from,
            reason=data.reason,request_key=data.request_key,request_fingerprint=fingerprint,
            replacement_assessment_id=new.id if new else None,created_by=actor_id)
        db.add(row);await db.flush()
        return row
