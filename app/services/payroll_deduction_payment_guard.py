"""Prevent payment paths from silently skipping unresolved deductions.

Called while holding the payroll company lock. Until the operational withholding
ledger exists, an effective instruction must stop a new payment. A zero monthly
result or a successful technical preview is not clearance for a later payment.
"""
from sqlalchemy import or_, select

from app.models.employment_contract import EmploymentContract
from app.models.payroll_deduction import PayrollDeductionInstruction


async def require_payment_without_unresolved_deductions(
    db, *, company_id, employee_id, payment_date, error_type, advance_id=None, disbursement_id=None,
):
    from app.services.payroll_deduction_execution_service import preparing, validate_payment_batch
    if preparing(db) and advance_id is None and disbursement_id is None:
        return
    try:
        if await validate_payment_batch(db, company_id=company_id, employee_id=employee_id,
            payment_date=payment_date, advance_id=advance_id, disbursement_id=disbursement_id):
            return
    except ValueError as exc:
        raise error_type(str(exc)) from exc
    instruction_id = await db.scalar(
        select(PayrollDeductionInstruction.id)
        .join(EmploymentContract, (
            EmploymentContract.company_id == PayrollDeductionInstruction.company_id
        ) & (
            EmploymentContract.id == PayrollDeductionInstruction.employment_contract_id
        ))
        .where(
            PayrollDeductionInstruction.company_id == company_id,
            EmploymentContract.employee_id == employee_id,
            PayrollDeductionInstruction.effective_from <= payment_date,
            or_(PayrollDeductionInstruction.effective_to.is_(None),
                PayrollDeductionInstruction.effective_to >= payment_date),
        ).limit(1)
    )
    if instruction_id is not None:
        raise error_type(
            'Payment has an effective deduction instruction; operational '
            'per-payment withholding is required before posting'
        )
