from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.api.permissions import require_company_permission
from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_status_event import (
    VatDeclarationStatusEvent,
)
from app.schemas.vat_declaration import (
    VatDeclarationBuildRequest,
    VatDeclarationDetailRead,
    VatDeclarationLifecycleRequest,
    VatDeclarationRead,
    VatDeclarationStatusEventRead,
)
from app.services.vat_declaration_lifecycle_service import (
    append_vat_declaration_status,
)
from app.services.vat_declaration_orchestration_service import (
    build_vat_declaration_idempotent,
)


router = APIRouter(
    prefix="/companies/{company_id}/vat-declarations",
    tags=["VAT declarations"],
)


async def _load_detail(
    db: AsyncSession,
    *,
    company_id: int,
    declaration_id: int,
) -> VatDeclaration:
    result = await db.execute(
        select(VatDeclaration)
        .options(
            selectinload(
                VatDeclaration.source_lines
            ),
            selectinload(
                VatDeclaration.carry_forward_lines
            ),
            selectinload(
                VatDeclaration.status_events
            ),
        )
        .where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.id == declaration_id,
        )
    )

    declaration = result.scalar_one_or_none()

    if declaration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="VAT declaration not found",
        )

    return declaration


@router.post(
    "",
    response_model=VatDeclarationRead,
    status_code=status.HTTP_201_CREATED,
)
async def build_declaration(
    company_id: int,
    data: VatDeclarationBuildRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
):
    try:
        declaration = await build_vat_declaration_idempotent(
            db,
            company_id=company_id,
            reporting_year=data.reporting_year,
            reporting_month=data.reporting_month,
            source_cutoff_at=data.source_cutoff_at,
            created_by=current_user.id,
            idempotency_key=idempotency_key,
        )

        await db.commit()
        await db.refresh(declaration)

        return declaration

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VAT declaration data conflict",
        ) from exc

    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "",
    response_model=list[VatDeclarationRead],
)
async def list_declarations(
    company_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    result = await db.scalars(
        select(VatDeclaration)
        .where(
            VatDeclaration.company_id == company_id
        )
        .order_by(
            VatDeclaration.reporting_year.desc(),
            VatDeclaration.reporting_month.desc(),
            VatDeclaration.snapshot_version.desc(),
            VatDeclaration.id.desc(),
        ).offset(offset).limit(limit)
    )

    return list(result.all())


@router.get(
    "/{declaration_id}",
    response_model=VatDeclarationDetailRead,
)
async def get_declaration(
    company_id: int,
    declaration_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    return await _load_detail(
        db,
        company_id=company_id,
        declaration_id=declaration_id,
    )


@router.post(
    "/{declaration_id}/status-events",
    response_model=VatDeclarationStatusEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def append_status_event(
    company_id: int,
    declaration_id: int,
    data: VatDeclarationLifecycleRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    try:
        event = await append_vat_declaration_status(
            db=db,
            company_id=company_id,
            vat_declaration_id=declaration_id,
            new_status=data.status,
            event_date=data.event_date,
            created_by=current_user.id,
            reference=data.reference,
        )

        await db.commit()
        await db.refresh(event)

        return event

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VAT declaration lifecycle conflict",
        ) from exc

    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{declaration_id}/status-events",
    response_model=list[VatDeclarationStatusEventRead],
)
async def list_status_events(
    company_id: int,
    declaration_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    exists = await db.scalar(
        select(VatDeclaration.id).where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.id == declaration_id,
        )
    )

    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="VAT declaration not found",
        )

    result = await db.scalars(
        select(VatDeclarationStatusEvent)
        .where(
            VatDeclarationStatusEvent.company_id
            == company_id,
            VatDeclarationStatusEvent.vat_declaration_id
            == declaration_id,
        )
        .order_by(
            VatDeclarationStatusEvent.created_at.asc(),
            VatDeclarationStatusEvent.id.asc(),
        )
    )

    return list(result.all())


# S11 export endpoints.
# Export generation is deliberately separate from submission/acceptance.

from fastapi import Response

from app.schemas.vat_declaration import (
    VatDeclarationExportArtifactRead,
)
from app.services.vat_declaration_export_persistence_service import (
    create_or_get_vat_declaration_export,
    get_vat_declaration_export,
    list_vat_declaration_exports,
)


@router.post(
    "/{declaration_id}/exports",
    response_model=VatDeclarationExportArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_declaration_export(
    company_id: int,
    declaration_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    try:
        artifact = await create_or_get_vat_declaration_export(
            db,
            company_id=company_id,
            vat_declaration_id=declaration_id,
            created_by=current_user.id,
        )

        await db.commit()
        await db.refresh(artifact)

        return artifact

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VAT declaration export conflict",
        ) from exc

    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.get(
    "/{declaration_id}/exports",
    response_model=list[VatDeclarationExportArtifactRead],
)
async def list_declaration_exports(
    company_id: int,
    declaration_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    return await list_vat_declaration_exports(
        db,
        company_id=company_id,
        vat_declaration_id=declaration_id,
    )


@router.get(
    "/{declaration_id}/exports/{export_id}",
    response_model=VatDeclarationExportArtifactRead,
)
async def get_declaration_export_metadata(
    company_id: int,
    declaration_id: int,
    export_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    return await get_vat_declaration_export(
        db,
        company_id=company_id,
        vat_declaration_id=declaration_id,
        export_id=export_id,
    )


@router.get(
    "/{declaration_id}/exports/{export_id}/content",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/xml": {},
            },
            "description": (
                "Stored deterministic VAT declaration XML export"
            ),
        }
    },
)
async def get_declaration_export_content(
    company_id: int,
    declaration_id: int,
    export_id: int,
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    artifact = await get_vat_declaration_export(
        db,
        company_id=company_id,
        vat_declaration_id=declaration_id,
        export_id=export_id,
    )

    return Response(
        content=artifact.payload,
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{artifact.file_name}"'
            ),
            "ETag": f'"{artifact.payload_sha256}"',
            "X-Content-SHA256": artifact.payload_sha256,
            "X-Official-XSD-Verified": (
                "true"
                if artifact.official_xsd_verified
                else "false"
            ),
        },
    )
