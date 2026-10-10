"""Company-scoped payroll tax documentary evidence API."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import require_company_permission
from app.core.database import get_db

from app.models.user import User
from app.models.payroll_employee_tax_evidence import (
    PayrollEmployeeTaxEvidence,
)

from app.schemas.payroll_tax_evidence import (
    TaxEvidenceCreate,
    TaxEvidenceRead,
    TaxEvidenceVerify,
)

from app.services.payroll_tax_evidence_service import (
    PayrollTaxEvidenceError,
    create_tax_evidence,
    get_tax_evidence,
    verify_tax_evidence,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["Payroll tax evidence"],
)


def _raise_evidence_error(exc: PayrollTaxEvidenceError):
    message = str(exc)

    if "not found" in message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        ) from exc

    if "already been reviewed" in message:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message,
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=message,
    ) from exc


@router.post(
    "/payroll-tax-evidence",
    response_model=TaxEvidenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_tax_evidence(
    company_id: int,
    data: TaxEvidenceCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        row = await create_tax_evidence(
            db,
            company_id=company_id,
            created_by=actor.id,
            **data.model_dump(),
        )

        await db.commit()
        await db.refresh(row)

        return row

    except PayrollTaxEvidenceError as exc:
        await db.rollback()
        _raise_evidence_error(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tax evidence database conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/payroll-tax-evidence",
    response_model=list[TaxEvidenceRead],
)
async def list_tax_evidence(
    company_id: int,
    employee_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.read")
    ),
):
    query = select(PayrollEmployeeTaxEvidence).where(
        PayrollEmployeeTaxEvidence.company_id == company_id
    )

    if employee_id is not None:
        query = query.where(
            PayrollEmployeeTaxEvidence.employee_id == employee_id
        )

    query = query.order_by(
        PayrollEmployeeTaxEvidence.id
    )

    return list((await db.scalars(query)).all())


@router.get(
    "/payroll-tax-evidence/{evidence_id}",
    response_model=TaxEvidenceRead,
)
async def read_tax_evidence(
    company_id: int,
    evidence_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await get_tax_evidence(
            db,
            company_id=company_id,
            evidence_id=evidence_id,
        )

    except PayrollTaxEvidenceError as exc:
        _raise_evidence_error(exc)


@router.post(
    "/payroll-tax-evidence/{evidence_id}/verify",
    response_model=TaxEvidenceRead,
)
async def post_verify_tax_evidence(
    company_id: int,
    evidence_id: int,
    data: TaxEvidenceVerify,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        row = await verify_tax_evidence(
            db,
            company_id=company_id,
            evidence_id=evidence_id,
            verified_by=actor.id,
            approved=data.approved,
        )

        await db.commit()
        await db.refresh(row)

        return row

    except PayrollTaxEvidenceError as exc:
        await db.rollback()
        _raise_evidence_error(exc)

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tax evidence verification conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise
