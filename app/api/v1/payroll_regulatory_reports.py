from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.payroll_regulatory_report import (
    PayrollRegulatoryReportRead,
    PayrollRegulatoryReadinessRead,
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


@router.get('/payroll-periods/{payroll_period_id}/regulatory-readiness/{report_kind}',
            response_model=PayrollRegulatoryReadinessRead)
async def read_regulatory_readiness(
    company_id: int, payroll_period_id: int, report_kind: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_company_permission('employees.read')),
):
    from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
    try:
        return await payroll_regulatory_readiness(db, company_id=company_id,
            payroll_period_id=payroll_period_id, report_kind=report_kind)
    except PayrollRegulatoryReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PayrollRegulatoryReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/payroll-regulatory-reports/{report_id}/4df-review.csv",
    response_class=Response,
    summary="Export internal 4DF financial review evidence",
)
async def download_4df_financial_review(
    company_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(
        require_company_permission("employees.read")
    ),
):
    """
    Internal payroll evidence only.

    This endpoint does not produce or approve a statutory filing.
    Access is subject to the ERP's company-scoped employee
    read permission, and the service also scopes database reads
    by company_id.
    """
    from app.services.payroll_regulatory_4df_review_export import (
        export_4df_financial_review,
    )

    try:
        document = await export_4df_financial_review(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
        )
    except PayrollRegulatoryReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PayrollRegulatoryReportError as exc:
        if str(exc) == "Regulatory report not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Regulatory report not found",
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    filename = (
        f"company-{company_id}-report-{report_id}-"
        "4df-INTERNAL-REVIEW.csv"
    )

    return Response(
        content=document.csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-4DF-Document-Type":
                "INTERNAL_REVIEW_NOT_FOR_FILING",
            "X-Content-SHA256": document.sha256,
            "X-4DF-Row-Count": str(document.row_count),
            "X-4DF-Calculation-Count":
                str(document.calculation_count),
        },
    )
