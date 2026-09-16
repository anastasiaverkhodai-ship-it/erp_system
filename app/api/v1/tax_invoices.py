from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import require_company_permission
from app.core.database import get_db
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_registration_event import (
    TaxInvoiceRegistrationEvent,
)
from app.schemas.tax_invoice import (
    InputTaxInvoiceCreate,
    OutputTaxInvoiceCreate,
    TaxInvoiceRegistrationCreate,
    TaxInvoiceRegistrationResponse,
    TaxInvoiceResponse,
)
from app.services.tax_invoice_input_persistence_service import (
    InputTaxInvoiceError,
    InputTaxInvoiceLineAttestation,
    create_input_tax_invoice,
)
from app.services.tax_invoice_output_persistence_service import (
    TaxInvoiceOutputError,
    create_output_tax_invoice,
)
from app.services.tax_invoice_registration_lifecycle_service import (
    TaxInvoiceRegistrationError,
    append_tax_invoice_registration_event,
)


router = APIRouter(
    prefix="/companies/{company_id}/tax-invoices",
    tags=["Tax invoices"],
)


async def _commit_or_409(
    db: AsyncSession,
    awaitable,
):
    try:
        result = await awaitable
        await db.commit()
        return result
    except (
        TaxInvoiceOutputError,
        InputTaxInvoiceError,
        TaxInvoiceRegistrationError,
    ) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise


async def _create_output_with_initial_registration(
    db: AsyncSession,
    *,
    company_id: int,
    payload: OutputTaxInvoiceCreate,
    created_by: int,
) -> TaxInvoice:
    """
    Create/reuse the OUTPUT tax invoice and guarantee its initial
    PREPARED registration event in the same caller-owned transaction.

    If registration-event creation fails, the API rollback removes
    the newly created TaxInvoice and its lines as well.
    """

    invoice = await create_output_tax_invoice(
        db,
        company_id=company_id,
        source_kind=payload.source_kind,
        source_id=payload.source_id,
        document_number=payload.document_number,
        created_by=created_by,
    )

    existing_registration = await db.scalar(
        select(TaxInvoiceRegistrationEvent)
        .where(
            TaxInvoiceRegistrationEvent.company_id
            == company_id,
            TaxInvoiceRegistrationEvent.tax_invoice_id
            == invoice.id,
        )
        .order_by(
            TaxInvoiceRegistrationEvent.id
        )
        .limit(1)
    )

    if existing_registration is None:
        await append_tax_invoice_registration_event(
            db,
            company_id=company_id,
            tax_invoice_id=invoice.id,
            status="prepared",
            event_date=invoice.document_date,
            reference=None,
            created_by=created_by,
        )

    return invoice


@router.post(
    "/output",
    response_model=TaxInvoiceResponse,
)
async def create_output(
    company_id: int,
    payload: OutputTaxInvoiceCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    _=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    return await _commit_or_409(
        db,
        _create_output_with_initial_registration(
            db,
            company_id=company_id,
            payload=payload,
            created_by=user.id,
        ),
    )


@router.post(
    "/input",
    response_model=TaxInvoiceResponse,
)
async def create_input(
    company_id: int,
    payload: InputTaxInvoiceCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    _=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    lines = [
        InputTaxInvoiceLineAttestation(
            line_number=line.line_number,
            claim_id=line.claim_id,
            description=line.description,
            quantity=line.quantity,
            uom_code=line.uom_code,
            classification_kind=(
                line.classification_kind
            ),
            statutory_code=line.statutory_code,
        )
        for line in payload.lines
    ]

    return await _commit_or_409(
        db,
        create_input_tax_invoice(
            db,
            company_id=company_id,
            lines=lines,
            created_by=user.id,
        ),
    )


@router.get(
    "",
    response_model=list[TaxInvoiceResponse],
)
async def list_tax_invoices(
    company_id: int,
    after_id: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    return list(
        (
            await db.scalars(
                select(TaxInvoice)
                .where(
                    TaxInvoice.company_id
                    == company_id,
                    TaxInvoice.id > after_id,
                )
                .order_by(TaxInvoice.id)
                .limit(limit)
            )
        ).all()
    )


@router.post(
    "/{tax_invoice_id}/registration-events",
    response_model=TaxInvoiceRegistrationResponse,
)
async def append_registration(
    company_id: int,
    tax_invoice_id: int,
    payload: TaxInvoiceRegistrationCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    _=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    return await _commit_or_409(
        db,
        append_tax_invoice_registration_event(
            db,
            company_id=company_id,
            tax_invoice_id=tax_invoice_id,
            status=payload.status,
            event_date=payload.event_date,
            reference=payload.reference,
            created_by=user.id,
        ),
    )


@router.get(
    "/{tax_invoice_id}/registration-events",
    response_model=list[
        TaxInvoiceRegistrationResponse
    ],
)
async def registration_history(
    company_id: int,
    tax_invoice_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    exists = await db.scalar(
        select(TaxInvoice.id).where(
            TaxInvoice.company_id
            == company_id,
            TaxInvoice.id
            == tax_invoice_id,
        )
    )

    if exists is None:
        raise HTTPException(
            status_code=404,
            detail="Tax invoice not found",
        )

    return list(
        (
            await db.scalars(
                select(
                    TaxInvoiceRegistrationEvent
                )
                .where(
                    TaxInvoiceRegistrationEvent
                    .company_id
                    == company_id,
                    TaxInvoiceRegistrationEvent
                    .tax_invoice_id
                    == tax_invoice_id,
                )
                .order_by(
                    TaxInvoiceRegistrationEvent
                    .event_date,
                    TaxInvoiceRegistrationEvent.id,
                )
            )
        ).all()
    )
