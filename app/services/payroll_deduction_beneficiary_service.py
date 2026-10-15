"""Verified recipient lifecycle; it does not itself authorize wage withholding."""
from datetime import date, datetime, timezone
from sqlalchemy import select, or_
from app.models.account import Account
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_beneficiary import PayrollDeductionBeneficiary as Recipient
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_deduction_rule_approval_write_service import _require_permission, PayrollDeductionRuleApprovalWriteError
from app.services.payroll_deduction_evidence_policy import resolve_verified_deduction_classification, PayrollDeductionEvidencePolicyError
from app.schemas.payroll_deduction_beneficiary import DeductionBeneficiaryCreate


class DeductionBeneficiaryError(ValueError):
    pass


class DeductionBeneficiaryNotFound(DeductionBeneficiaryError):
    pass


async def _permission(db, company_id, actor_id):
    try:
        await _require_permission(db=db, company_id=company_id, actor_id=actor_id, permission='employees.manage')
    except PayrollDeductionRuleApprovalWriteError as exc:
        raise DeductionBeneficiaryError(str(exc)) from exc


async def get_beneficiary(db, *, company_id, beneficiary_id):
    row = await db.scalar(select(Recipient).where(Recipient.company_id == company_id,
        Recipient.id == beneficiary_id).execution_options(populate_existing=True))
    if row is None:
        raise DeductionBeneficiaryNotFound('Deduction recipient not found in company')
    return row


async def _validate_sources(db, company_id, instruction_id, data):
    instruction = await db.scalar(select(PayrollDeductionInstruction).where(
        PayrollDeductionInstruction.company_id == company_id, PayrollDeductionInstruction.id == instruction_id))
    if instruction is None:
        raise DeductionBeneficiaryNotFound('Deduction instruction not found in company')
    if instruction.currency_code != 'UAH':
        raise DeductionBeneficiaryError('Deduction recipient supports UAH only')
    if data.valid_from < instruction.effective_from or (instruction.effective_to is not None and
            (data.valid_to is None or data.valid_to > instruction.effective_to)):
        raise DeductionBeneficiaryError('Recipient validity exceeds instruction validity')
    account = await db.scalar(select(Account).where(Account.company_id == company_id,
        Account.id == data.account_id, Account.is_active.is_(True), Account.is_postable.is_(True)))
    if account is None:
        raise DeductionBeneficiaryError('Active company settlement account required')
    if data.settlement_kind == 'recipient_payment':
        if account.account_type != 'liability' or not account.code.startswith('685'):
            raise DeductionBeneficiaryError('Recipient payment requires a 685 liability account')
    elif account.account_type != 'asset' or not account.code.startswith('377'):
        raise DeductionBeneficiaryError('Employer recovery requires a 377 receivable account')
    return instruction


@serialized_payroll_mutation(DeductionBeneficiaryError)
async def create_beneficiary(db, *, company_id, instruction_id, data, actor_id):
    await _permission(db, company_id, actor_id)
    data = DeductionBeneficiaryCreate.model_validate(data.model_dump() if isinstance(data, DeductionBeneficiaryCreate) else data)
    old = await db.scalar(select(Recipient).where(Recipient.company_id == company_id,
        Recipient.request_key == data.request_key).execution_options(populate_existing=True))
    if old is not None:
        if old.instruction_id != instruction_id or any(getattr(old, k) != v for k, v in data.model_dump().items()):
            raise DeductionBeneficiaryError('Request key already contains different recipient details')
        return old
    await _validate_sources(db, company_id, instruction_id, data)
    row = Recipient(company_id=company_id, instruction_id=instruction_id,
        created_by=actor_id, status='pending', **data.model_dump())
    db.add(row)
    await db.flush()
    return row


@serialized_payroll_mutation(DeductionBeneficiaryError)
async def verify_beneficiary(db, *, company_id, beneficiary_id, actor_id):
    await _permission(db, company_id, actor_id)
    row = await get_beneficiary(db, company_id=company_id, beneficiary_id=beneficiary_id)
    if row.status == 'verified':
        return row
    if row.status != 'pending':
        raise DeductionBeneficiaryError('Revoked recipient cannot be verified again')
    if row.created_by == actor_id:
        raise DeductionBeneficiaryError('An independent person must verify recipient details')
    await _validate_sources(db, company_id, row.instruction_id, row)
    try:
        category = await resolve_verified_deduction_classification(db, company_id=company_id,
            payroll_deduction_instruction_id=row.instruction_id)
    except PayrollDeductionEvidencePolicyError as exc:
        raise DeductionBeneficiaryError('Verified deduction document is required') from exc
    if (category.category == 'employer_statutory') != (row.settlement_kind == 'employer_recovery'):
        raise DeductionBeneficiaryError('Recipient settlement kind conflicts with verified legal category')
    overlap = await db.scalar(select(Recipient.id).where(Recipient.company_id == company_id,
        Recipient.instruction_id == row.instruction_id, Recipient.id != row.id, Recipient.status == 'verified',
        or_(Recipient.valid_to.is_(None), Recipient.valid_to >= row.valid_from),
        Recipient.valid_from <= (row.valid_to or date.max)).limit(1))
    if overlap is not None:
        raise DeductionBeneficiaryError('Verified recipient validity overlaps another version')
    row.status, row.verified_by, row.verified_at = 'verified', actor_id, datetime.now(timezone.utc)
    await db.flush()
    return row


@serialized_payroll_mutation(DeductionBeneficiaryError)
async def revoke_beneficiary(db, *, company_id, beneficiary_id, actor_id):
    await _permission(db, company_id, actor_id)
    row = await get_beneficiary(db, company_id=company_id, beneficiary_id=beneficiary_id)
    if row.status == 'revoked':
        return row
    if row.status != 'verified':
        raise DeductionBeneficiaryError('Only a verified recipient may be revoked')
    row.status, row.revoked_by, row.revoked_at = 'revoked', actor_id, datetime.now(timezone.utc)
    await db.flush()
    return row


async def resolve_verified_beneficiary(db, *, company_id, instruction_id, payment_date):
    """Resolve current execution details, rechecking their documentary sources.

    Historical ledger readers use their saved beneficiary ID instead; revocation
    must never rewrite already posted recipient analytics.
    """
    rows = list((await db.scalars(select(Recipient).where(
        Recipient.company_id == company_id, Recipient.instruction_id == instruction_id,
        Recipient.status == 'verified', Recipient.valid_from <= payment_date,
        or_(Recipient.valid_to.is_(None), Recipient.valid_to >= payment_date),
    ).execution_options(populate_existing=True))).all())
    if len(rows) != 1:
        raise DeductionBeneficiaryError('Exactly one verified recipient is required on payment date')
    row = rows[0]
    instruction = await _validate_sources(db, company_id, instruction_id, row)
    if not instruction.effective_from <= payment_date or (instruction.effective_to is not None and payment_date > instruction.effective_to):
        raise DeductionBeneficiaryError('Instruction is not effective on payment date')
    try:
        category = await resolve_verified_deduction_classification(db, company_id=company_id,
            payroll_deduction_instruction_id=instruction_id)
    except PayrollDeductionEvidencePolicyError as exc:
        raise DeductionBeneficiaryError('Verified deduction document is required') from exc
    if (category.category == 'employer_statutory') != (row.settlement_kind == 'employer_recovery'):
        raise DeductionBeneficiaryError('Recipient settlement kind conflicts with verified legal category')
    return row
