from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.api.permissions import require_company_permission
from app.models.user import User
from app.schemas.journal_entry import JournalEntryResponse
from app.schemas.payroll_deduction import (
    PayrollDeductionInstructionCreate,
    PayrollDeductionInstructionRead,
    PayrollDeductionResultRead,
)
from app.services.payroll_deduction_accounting_service import (
    PayrollDeductionAccountingError,
    PayrollDeductionAccountingConflictError,
    PayrollDeductionAccountingNotFoundError,
    generate_and_post_payroll_deduction_journal,
    get_payroll_deduction_journal,
    reverse_payroll_deduction_journal,
)
from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultError,
    PayrollDeductionResultConflictError,
    PayrollDeductionResultNotFoundError,
    calculate_payroll_deduction_result,
    get_payroll_deduction_result,
)
from app.services.payroll_deduction_service import (
    PayrollDeductionError,
    create_payroll_deduction_instruction,
    get_payroll_deduction_instruction,
    list_payroll_deduction_instructions,
)

router = APIRouter(tags=["payroll-deductions"])


def _error(exc: PayrollDeductionError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/companies/{company_id}/payroll-deductions",
    response_model=PayrollDeductionInstructionRead,
    status_code=201,
)
async def create_instruction(
    company_id: int,
    data: PayrollDeductionInstructionCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        row = await create_payroll_deduction_instruction(
            db,
            company_id=company_id,
            data=data,
            created_by=actor.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except PayrollDeductionError as exc:
        await db.rollback()
        raise _error(exc) from exc


@router.get(
    "/companies/{company_id}/payroll-deductions/{instruction_id}",
    response_model=PayrollDeductionInstructionRead,
)
async def get_instruction(
    company_id: int,
    instruction_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.read")
    ),
):
    row = await get_payroll_deduction_instruction(
        db,
        company_id=company_id,
        instruction_id=instruction_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Deduction not found")
    return row


@router.get(
    "/companies/{company_id}/employment-contracts/"
    "{employment_contract_id}/payroll-deductions",
    response_model=list[PayrollDeductionInstructionRead],
)
async def list_instructions(
    company_id: int,
    employment_contract_id: int,
    active_on: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.read")
    ),
):
    return await list_payroll_deduction_instructions(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        active_on=active_on,
    )


@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/deduction-result",
    response_model=PayrollDeductionResultRead,
    status_code=status.HTTP_201_CREATED,
)
async def api_calculate_payroll_deduction_result(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        row = await calculate_payroll_deduction_result(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            calculated_by=actor.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except PayrollDeductionResultError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/deduction-result",
    response_model=PayrollDeductionResultRead,
)
async def api_get_payroll_deduction_result(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.read")
    ),
):
    row = await get_payroll_deduction_result(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Payroll deduction result not found",
        )

    return row


@router.get(
    "/companies/{company_id}/payroll-deduction-results/"
    "{payroll_deduction_result_id}/accounting-journal",
    response_model=JournalEntryResponse,
)
async def api_get_payroll_deduction_journal(
    company_id: int,
    payroll_deduction_result_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("journal_entries.read")
    ),
):
    row = await get_payroll_deduction_journal(
        db,
        company_id=company_id,
        payroll_deduction_result_id=payroll_deduction_result_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Payroll deduction accounting journal not found",
        )

    return row


@router.post(
    "/companies/{company_id}/payroll-deduction-results/"
    "{payroll_deduction_result_id}/accounting-journal",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_post_payroll_deduction_journal(
    company_id: int,
    payroll_deduction_result_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.create")
    ),
    approval: User = Depends(
        require_company_permission("journal_entries.approve")
    ),
):
    try:
        row = await generate_and_post_payroll_deduction_journal(
            db,
            company_id=company_id,
            payroll_deduction_result_id=payroll_deduction_result_id,
            created_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except PayrollDeductionResultConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except PayrollDeductionAccountingError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/companies/{company_id}/payroll-deduction-results/"
    "{payroll_deduction_result_id}/accounting-journal/reverse",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_reverse_payroll_deduction_journal(
    company_id: int,
    payroll_deduction_result_id: int,
    reversal_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("journal_entries.reverse")
    ),
):
    try:
        row = await reverse_payroll_deduction_journal(
            db,
            company_id=company_id,
            payroll_deduction_result_id=payroll_deduction_result_id,
            reversal_date=reversal_date,
            reversed_by=current_user.id,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except PayrollDeductionAccountingError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


# Verified recipients are separate from the technical remittance previews.
from app.schemas.payroll_deduction_beneficiary import DeductionBeneficiaryCreate, DeductionBeneficiaryRead
from app.services.payroll_deduction_beneficiary_service import (
    create_beneficiary, get_beneficiary, verify_beneficiary, revoke_beneficiary,
    DeductionBeneficiaryError, DeductionBeneficiaryNotFound,
)


def _beneficiary_error(exc):
    return HTTPException(status_code=404 if isinstance(exc, DeductionBeneficiaryNotFound) else 409,
                         detail=str(exc))


@router.post('/companies/{company_id}/payroll-deductions/{instruction_id}/beneficiaries',
             response_model=DeductionBeneficiaryRead, status_code=201)
async def api_create_deduction_beneficiary(company_id: int, instruction_id: int,
    data: DeductionBeneficiaryCreate, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await create_beneficiary(db, company_id=company_id, instruction_id=instruction_id,
                                      data=data, actor_id=actor.id)
        await db.commit()
        await db.refresh(row)
        return row
    except DeductionBeneficiaryError as exc:
        await db.rollback()
        raise _beneficiary_error(exc) from exc


@router.get('/companies/{company_id}/payroll-deduction-beneficiaries/{beneficiary_id}',
            response_model=DeductionBeneficiaryRead)
async def api_get_deduction_beneficiary(company_id: int, beneficiary_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    try:
        return await get_beneficiary(db, company_id=company_id, beneficiary_id=beneficiary_id)
    except DeductionBeneficiaryError as exc:
        raise _beneficiary_error(exc) from exc


@router.post('/companies/{company_id}/payroll-deduction-beneficiaries/{beneficiary_id}/verify',
             response_model=DeductionBeneficiaryRead)
async def api_verify_deduction_beneficiary(company_id: int, beneficiary_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await verify_beneficiary(db, company_id=company_id, beneficiary_id=beneficiary_id, actor_id=actor.id)
        await db.commit()
        await db.refresh(row)
        return row
    except DeductionBeneficiaryError as exc:
        await db.rollback()
        raise _beneficiary_error(exc) from exc


@router.post('/companies/{company_id}/payroll-deduction-beneficiaries/{beneficiary_id}/revoke',
             response_model=DeductionBeneficiaryRead)
async def api_revoke_deduction_beneficiary(company_id: int, beneficiary_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await revoke_beneficiary(db, company_id=company_id, beneficiary_id=beneficiary_id, actor_id=actor.id)
        await db.commit()
        await db.refresh(row)
        return row
    except DeductionBeneficiaryError as exc:
        await db.rollback()
        raise _beneficiary_error(exc) from exc


from app.schemas.payroll_deduction_execution import AssessmentCreate, WithholdingPaymentCreate, RecipientRemittanceCreate
from app.services.payroll_deduction_execution_service import create_assessment, execute_payment, recipient_balances, DeductionExecutionError
from app.services.payroll_recipient_remittance_service import register_recipient_remittance, reverse_recipient_remittance


def _execution_record(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != 'creation_xid'}


@router.post('/companies/{company_id}/payroll-deduction-assessments', status_code=201)
async def api_create_assessment(company_id: int, data: AssessmentCreate, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await create_assessment(db, company_id=company_id, data=data, actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-withholding-payments', status_code=201)
async def api_execute_withholding_payment(company_id: int, data: WithholdingPaymentCreate,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    from app.services.payroll_advance_service import PayrollAdvanceError
    from app.services.payroll_disbursement_service import PayrollDisbursementError
    from app.services.accounting_posting import AccountingPostingError
    try:
        row = await execute_payment(db, company_id=company_id, data=data, actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except (ValueError, PayrollAdvanceError, PayrollDisbursementError, AccountingPostingError) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/companies/{company_id}/payroll-recipient-balances')
async def api_recipient_balances(company_id: int, as_of: date = Query(...),
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    try:
        return await recipient_balances(db, company_id=company_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-recipient-remittances', status_code=201)
async def api_register_recipient_remittance(company_id: int, data: RecipientRemittanceCreate,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    try:
        row = await register_recipient_remittance(db, company_id=company_id, data=data, actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-recipient-remittances/{remittance_id}/reverse')
async def api_reverse_recipient_remittance(company_id: int, remittance_id: int, reversal_date: date,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    from app.services.accounting_reversal import AccountingReversalError
    try:
        row = await reverse_recipient_remittance(db, company_id=company_id, remittance_id=remittance_id,
            reversal_date=reversal_date, actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except (ValueError, AccountingReversalError) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/companies/{company_id}/payroll-deductions/{instruction_id}/assessments')
async def api_list_deduction_assessments(company_id: int, instruction_id: int,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    from sqlalchemy import select
    from app.models.payroll_deduction_execution import PayrollDeductionAssessment
    instruction = await get_payroll_deduction_instruction(db,company_id=company_id,instruction_id=instruction_id)
    if instruction is None:
        raise HTTPException(status_code=404,detail='Deduction not found')
    rows = await db.scalars(select(PayrollDeductionAssessment).where(
        PayrollDeductionAssessment.company_id==company_id,
        PayrollDeductionAssessment.instruction_id==instruction_id)
        .order_by(PayrollDeductionAssessment.id).offset(offset).limit(limit))
    return [_execution_record(row) for row in rows]


@router.get('/companies/{company_id}/payroll-withholding-payments/{batch_id}')
async def api_get_withholding_payment(company_id: int, batch_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    from sqlalchemy import select
    from app.models.payroll_deduction_execution import PayrollWithholdingBatch, PayrollWithholdingLine
    batch = await db.scalar(select(PayrollWithholdingBatch).where(
        PayrollWithholdingBatch.company_id==company_id,PayrollWithholdingBatch.id==batch_id))
    if batch is None:
        raise HTTPException(status_code=404,detail='Withholding payment not found')
    lines = await db.scalars(select(PayrollWithholdingLine).where(
        PayrollWithholdingLine.company_id==company_id,PayrollWithholdingLine.batch_id==batch.id)
        .order_by(PayrollWithholdingLine.id))
    return {**_execution_record(batch),'lines':[_execution_record(row) for row in lines]}


@router.get('/companies/{company_id}/payroll-recipient-remittances/{remittance_id}')
async def api_get_recipient_remittance(company_id: int, remittance_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.read'))):
    from sqlalchemy import select
    from app.models.payroll_deduction_execution import PayrollRecipientRemittance, PayrollRecipientRemittanceLine
    remittance = await db.scalar(select(PayrollRecipientRemittance).where(
        PayrollRecipientRemittance.company_id==company_id,PayrollRecipientRemittance.id==remittance_id))
    if remittance is None:
        raise HTTPException(status_code=404,detail='Recipient remittance not found')
    lines = await db.scalars(select(PayrollRecipientRemittanceLine).where(
        PayrollRecipientRemittanceLine.company_id==company_id,PayrollRecipientRemittanceLine.remittance_id==remittance.id)
        .order_by(PayrollRecipientRemittanceLine.id))
    return {**_execution_record(remittance),'allocations':[_execution_record(row) for row in lines]}


from app.schemas.payroll_deduction_execution import DeductionEvidenceCreate, DeductionRuleApprovalCreate


@router.post('/companies/{company_id}/payroll-deductions/{instruction_id}/legal-evidence', status_code=201)
async def api_create_deduction_evidence(company_id: int, instruction_id: int, data: DeductionEvidenceCreate,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    from app.services.payroll_deduction_evidence_write_service import create_pending_deduction_evidence
    try:
        row = await create_pending_deduction_evidence(db=db,company_id=company_id,
            payroll_deduction_instruction_id=instruction_id,created_by=actor.id,**data.model_dump())
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-deduction-evidence/{evidence_id}/verify')
async def api_verify_deduction_evidence(company_id: int, evidence_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('employees.manage'))):
    from sqlalchemy import select
    from app.models.payroll_deduction_legal_evidence import PayrollDeductionLegalEvidence
    from app.services.payroll_deduction_evidence_verification_service import verify_deduction_evidence
    try:
        evidence = await db.scalar(select(PayrollDeductionLegalEvidence).where(
            PayrollDeductionLegalEvidence.company_id==company_id,PayrollDeductionLegalEvidence.id==evidence_id)
            .with_for_update().execution_options(populate_existing=True))
        if evidence is None:
            raise ValueError('Deduction evidence not found')
        if evidence.created_by==actor.id:
            raise ValueError('An independent person must verify the legal document')
        row = await verify_deduction_evidence(db=db,company_id=company_id,evidence_id=evidence_id,verified_by=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-deduction-rule-approvals', status_code=201)
async def api_create_deduction_rule_approval(company_id: int, data: DeductionRuleApprovalCreate,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('payroll_deduction_rule_approval.create'))):
    from app.services.payroll_deduction_rule_approval_write_service import create_pending_rule_approval
    try:
        row = await create_pending_rule_approval(db=db,company_id=company_id,actor_id=actor.id,**data.model_dump())
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-deduction-rule-approvals/{approval_id}/approve')
async def api_approve_deduction_rule(company_id: int, approval_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('payroll_deduction_rule_approval.approve'))):
    from app.services.payroll_deduction_rule_approval_write_service import approve_rule_approval
    try:
        row = await approve_rule_approval(db=db,company_id=company_id,approval_id=approval_id,actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/companies/{company_id}/payroll-deduction-rule-approvals/{approval_id}/revoke')
async def api_revoke_deduction_rule(company_id: int, approval_id: int,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_company_permission('payroll_deduction_rule_approval.revoke'))):
    from app.services.payroll_deduction_rule_approval_write_service import revoke_rule_approval
    try:
        row = await revoke_rule_approval(db=db,company_id=company_id,approval_id=approval_id,actor_id=actor.id)
        await db.commit(); await db.refresh(row)
        return _execution_record(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409,detail=str(exc)) from exc
