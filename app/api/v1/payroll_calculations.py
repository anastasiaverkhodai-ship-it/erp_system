from app.services.payroll_deduction_result_service import PayrollDeductionResultConflictError
from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.user import User
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payroll_calculation import (
    PayrollCalculationLineRead,
    PayrollMinimumWageContext,
    PayrollCalculationRead,
)
from app.services.payroll_calculation_service import (
    PayrollCalculationConflictError,
    PayrollCalculationDerivationError,
    PayrollCalculationError,
    PayrollCalculationLifecycleError,
    PayrollCalculationNotFoundError,
    calculate_payroll_input,
    get_payroll_calculation,
    list_payroll_calculation_lines,
    list_payroll_calculations,
)

from app.schemas.payroll_payslip import (
    PayrollPayslipLineRead,
    PayrollPayslipRead,
)
from app.services.payroll_payslip_service import (
    PayrollPayslipNotFoundError,
    PayrollPayslipValidationError,
    generate_payroll_payslip,
    get_payroll_payslip_by_calculation,
    list_payroll_payslip_lines,
)


router = APIRouter(tags=["payroll-calculations"])


def _raise_http(exc: PayrollCalculationError) -> None:
    if isinstance(exc, PayrollCalculationNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PayrollCalculationConflictError,
            PayrollCalculationLifecycleError,
            PayrollCalculationDerivationError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get(
    "/companies/{company_id}/payroll-periods/"
    "{payroll_period_id}/calculations",
    response_model=list[PayrollCalculationRead],
)
async def list_period_payroll_calculations(
    company_id: int,
    payroll_period_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_payroll_calculations(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)


@router.post(
    "/companies/{company_id}/payroll-inputs/"
    "{payroll_input_id}/calculate",
    response_model=PayrollCalculationRead,
    status_code=status.HTTP_200_OK,
)
async def calculate_company_payroll_input(
    company_id: int,
    payroll_input_id: int,
    data: PayrollMinimumWageContext | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        calculation = await calculate_payroll_input(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input_id,
            calculated_by=current_user.id,
            minimum_wage_context=data.model_dump(exclude_none=True) if data else None,
        )

        await db.commit()
        await db.refresh(calculation)

        return calculation

    except PayrollCalculationError as exc:
        await db.rollback()
        _raise_http(exc)

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}",
    response_model=PayrollCalculationRead,
)
async def get_company_payroll_calculation(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await get_payroll_calculation(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/lines",
    response_model=list[PayrollCalculationLineRead],
)
async def list_company_payroll_calculation_lines(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_payroll_calculation_lines(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
        )
    except PayrollCalculationError as exc:
        _raise_http(exc)

@router.post(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/payslip/generate",
    response_model=PayrollPayslipRead,
    status_code=status.HTTP_200_OK,
)
async def generate_company_payroll_payslip(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: User = Depends(
        require_company_permission("employees.manage")
    ),
) -> PayrollPayslipRead:
    try:
        payslip = await generate_payroll_payslip(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            actor_user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(payslip)
        return payslip
    except PayrollDeductionResultConflictError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except PayrollPayslipNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PayrollPayslipValidationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/payslip",
    response_model=PayrollPayslipRead,
)
async def get_company_payroll_payslip(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
) -> PayrollPayslipRead:
    payslip = await get_payroll_payslip_by_calculation(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if payslip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll payslip not found",
        )

    return payslip


@router.get(
    "/companies/{company_id}/payroll-calculations/"
    "{payroll_calculation_id}/payslip/lines",
    response_model=list[PayrollPayslipLineRead],
)
async def list_company_payroll_payslip_lines(
    company_id: int,
    payroll_calculation_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
) -> list[PayrollPayslipLineRead]:
    payslip = await get_payroll_payslip_by_calculation(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    if payslip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payroll payslip not found",
        )

    return await list_payroll_payslip_lines(
        db,
        company_id=company_id,
        payroll_payslip_id=payslip.id,
    )


from app.schemas.payroll_calculation import (
    PayrollCorrectionEvidenceRequest, PayrollMinimumWageCorrectionRequest,
)
from app.services.payroll_correction_source_evidence_service import (
    capture_payroll_correction_source, PayrollCorrectionEvidenceError,
)
from app.services.payroll_correction_preflight_service import (
    PayrollCorrectionPreflightError, PayrollCorrectionPreflightNotFound,
)
from app.services.payroll_correction_recalculation_service import recalculate_minimum_wage_correction
from app.services.payroll_correction_recalculation_contract import PayrollCorrectionRecalculationError
from app.services.payroll_correction_service import PayrollCorrectionError


_CORRECTION_ERRORS = (PayrollCorrectionEvidenceError, PayrollCorrectionPreflightError,
                      PayrollCorrectionRecalculationError, PayrollCorrectionError)


@router.post('/companies/{company_id}/payroll-calculations/{payroll_calculation_id}/correction-evidence')
async def payroll_correction_evidence(
    company_id: int, payroll_calculation_id: int, data: PayrollCorrectionEvidenceRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_company_permission('employees.manage')),
):
    try:
        return await capture_payroll_correction_source(db, company_id=company_id,
            original_payroll_calculation_id=payroll_calculation_id,
            request_key=data.request_key, reason=data.reason)
    except _CORRECTION_ERRORS as exc:
        raise HTTPException(status_code=404 if isinstance(exc, PayrollCorrectionPreflightNotFound) else 409,
                            detail=str(exc)) from exc
    finally:
        # Read-only evidence owns no lasting lock or transaction.
        await db.rollback()


@router.post('/companies/{company_id}/payroll-calculations/{payroll_calculation_id}/minimum-wage-correction',
             response_model=PayrollCalculationRead)
async def payroll_minimum_wage_correction(
    company_id: int, payroll_calculation_id: int, data: PayrollMinimumWageCorrectionRequest,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
    _: User = Depends(require_company_permission('employees.manage')),
):
    try:
        calculation, _link = await recalculate_minimum_wage_correction(db,
            company_id=company_id, original_payroll_calculation_id=payroll_calculation_id,
            calculated_by=current_user.id, request_key=data.request_key, reason=data.reason,
            corrected_minimum_wage_context=data.corrected_minimum_wage_context.model_dump(exclude_none=True),
            expected_source_fingerprint_sha256=data.expected_source_fingerprint_sha256)
        await db.commit()
        await db.refresh(calculation)
        return calculation
    except _CORRECTION_ERRORS as exc:
        await db.rollback()
        raise HTTPException(status_code=404 if isinstance(exc, PayrollCorrectionPreflightNotFound) else 409,
                            detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise


@router.get('/companies/{company_id}/payroll-calculations/{payroll_calculation_id}/payslip-history',
            response_model=list[PayrollPayslipRead])
async def get_company_payslip_history(company_id: int,payroll_calculation_id: int,
    db: AsyncSession=Depends(get_db),_: User=Depends(require_company_permission('employees.read'))):
    from sqlalchemy import select
    from app.models.payroll_payslip import PayrollPayslip
    return list((await db.scalars(select(PayrollPayslip).where(PayrollPayslip.company_id==company_id,
        PayrollPayslip.payroll_calculation_id==payroll_calculation_id).order_by(PayrollPayslip.revision))).all())


@router.get('/companies/{company_id}/payroll-payslips/{payslip_id}/lines',response_model=list[PayrollPayslipLineRead])
async def get_company_payslip_revision_lines(company_id: int,payslip_id: int,
    db: AsyncSession=Depends(get_db),_: User=Depends(require_company_permission('employees.read'))):
    from app.services.payroll_payslip_service import get_payroll_payslip, PayrollPayslipNotFoundError
    try:
        await get_payroll_payslip(db,company_id=company_id,payslip_id=payslip_id)
        return await list_payroll_payslip_lines(db,company_id=company_id,payroll_payslip_id=payslip_id)
    except PayrollPayslipNotFoundError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
