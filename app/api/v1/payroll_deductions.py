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
