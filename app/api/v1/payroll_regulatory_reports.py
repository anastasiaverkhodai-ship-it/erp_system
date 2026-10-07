from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_regulatory_report import (
    PayrollRegulatoryReportRead,
    PayrollRegulatoryReportRowRead,
)
from app.api.permissions import require_company_permission
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
    PayrollRegulatoryReportNotFoundError,
    create_payroll_regulatory_report,
    generate_payroll_regulatory_report,
    list_payroll_regulatory_report_rows,
)


router = APIRouter(
    prefix="/companies/{company_id}",
    tags=["Payroll regulatory reporting"],
)


@router.post(
    "/payroll-periods/{payroll_period_id}/regulatory-reports/{report_kind}",
    response_model=PayrollRegulatoryReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    company_id: int,
    payroll_period_id: int,
    report_kind: str,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        report = await create_payroll_regulatory_report(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            report_kind=report_kind,
            created_by=actor.id,
        )
        await db.commit()
        await db.refresh(report)
        return report
    except PayrollRegulatoryReportNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PayrollRegulatoryReportError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/payroll-regulatory-reports/{report_id}/generate",
    response_model=PayrollRegulatoryReportRead,
)
async def generate_report(
    company_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(
        require_company_permission("employees.manage")
    ),
):
    try:
        report = await generate_payroll_regulatory_report(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            generated_by=actor.id,
        )
        await db.commit()
        await db.refresh(report)
        return report
    except PayrollRegulatoryReportNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PayrollRegulatoryReportError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/payroll-regulatory-reports/{report_id}/rows",
    response_model=list[PayrollRegulatoryReportRowRead],
)
async def read_report_rows(
    company_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    try:
        return await list_payroll_regulatory_report_rows(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
        )
    except PayrollRegulatoryReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
