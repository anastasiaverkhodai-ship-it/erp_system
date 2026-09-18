from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.models.tax_invoice_correction import (
    TaxInvoiceCorrection,
)
from app.models.tax_invoice_correction_line import (
    TaxInvoiceCorrectionLine,
)
from app.models.tax_invoice_correction_registration_event import (
    TaxInvoiceCorrectionRegistrationEvent,
)
from app.schemas.tax_invoice_correction import (
    InputTaxInvoiceCorrectionCreate,
    OutputTaxInvoiceCorrectionCreate,
    TaxInvoiceCorrectionDetailResponse,
    TaxInvoiceCorrectionLineResponse,
    TaxInvoiceCorrectionRegistrationCreate,
    TaxInvoiceCorrectionRegistrationResponse,
    TaxInvoiceCorrectionResponse,
)
from app.services.idempotency_completion_service import (
    IdempotencyCompletionError,
)
from app.services.idempotency_consistency_validator import (
    IdempotencyConsistencyError,
)
from app.services.idempotency_request_validator import (
    IdempotencyRequestValidationError,
)
from app.services.idempotency_reservation_service import (
    IdempotencyReservationError,
)
from app.services.tax_invoice_correction_orchestration_service import (
    TaxInvoiceCorrectionOrchestrationError,
    append_tax_invoice_correction_registration_idempotent,
    create_tax_invoice_correction_idempotent,
)
from app.services.tax_invoice_correction_persistence_service import (
    TaxInvoiceCorrectionPersistenceError,
)
from app.services.tax_invoice_correction_registration_lifecycle_service import (
    TaxInvoiceCorrectionRegistrationError,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    TaxInvoiceCorrectionLineSource,
    TaxInvoiceCorrectionSourceError,
)


router = APIRouter(
    prefix="/companies/{company_id}/tax-invoice-corrections",
    tags=["tax-invoice-corrections"],
)


DOMAIN_ERRORS = (
    TaxInvoiceCorrectionOrchestrationError,
    TaxInvoiceCorrectionPersistenceError,
    TaxInvoiceCorrectionRegistrationError,
    TaxInvoiceCorrectionSourceError,
    IdempotencyReservationError,
    IdempotencyCompletionError,
    IdempotencyConsistencyError,
    IdempotencyRequestValidationError,
    ValueError,
)


async def _commit_id_or_409(
    db: AsyncSession,
    operation: Awaitable[Any],
) -> int:
    try:
        result = await operation
        result_id = int(
            result.id
        )
        await db.commit()
        return result_id

    except DOMAIN_ERRORS as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Tax-invoice correction data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


def _source_line(
    item,
) -> TaxInvoiceCorrectionLineSource:
    return TaxInvoiceCorrectionLineSource(
        line_number=item.line_number,
        source_kind=item.source_kind,
        source_id=item.source_id,
        reason_code=item.reason_code,
        replacement=item.replacement.model_dump(exclude_none=True) if item.replacement else None,
        tax_credit_evidence_id=getattr(
            item,
            "tax_credit_evidence_id",
            None,
        ),
    )


async def _detail(
    db: AsyncSession,
    *,
    company_id: int,
    tax_invoice_correction_id: int,
) -> TaxInvoiceCorrectionDetailResponse:
    header = await db.scalar(
        select(
            TaxInvoiceCorrection
        ).where(
            TaxInvoiceCorrection.company_id
            == company_id,
            TaxInvoiceCorrection.id
            == tax_invoice_correction_id,
        )
    )

    if header is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tax-invoice correction not found",
        )

    lines = list(
        (
            await db.scalars(
                select(
                    TaxInvoiceCorrectionLine
                )
                .where(
                    TaxInvoiceCorrectionLine.company_id
                    == company_id,
                    TaxInvoiceCorrectionLine
                    .tax_invoice_correction_id
                    == tax_invoice_correction_id,
                )
                .order_by(
                    TaxInvoiceCorrectionLine.line_number,
                    TaxInvoiceCorrectionLine.id,
                )
            )
        ).all()
    )

    registration_events = list(
        (
            await db.scalars(
                select(
                    TaxInvoiceCorrectionRegistrationEvent
                )
                .where(
                    TaxInvoiceCorrectionRegistrationEvent.company_id
                    == company_id,
                    TaxInvoiceCorrectionRegistrationEvent
                    .tax_invoice_correction_id
                    == tax_invoice_correction_id,
                )
                .order_by(
                    TaxInvoiceCorrectionRegistrationEvent.event_date,
                    TaxInvoiceCorrectionRegistrationEvent.id,
                )
            )
        ).all()
    )

    return TaxInvoiceCorrectionDetailResponse(
        header=TaxInvoiceCorrectionResponse.model_validate(
            header
        ),
        lines=[
            TaxInvoiceCorrectionLineResponse.model_validate(
                item
            )
            for item in lines
        ],
        registration_events=[
            TaxInvoiceCorrectionRegistrationResponse.model_validate(
                item
            )
            for item in registration_events
        ],
    )


@router.post(
    "/output",
    response_model=TaxInvoiceCorrectionDetailResponse,
)
async def create_output_correction(
    company_id: int,
    payload: OutputTaxInvoiceCorrectionCreate,
    db: AsyncSession = Depends(
        get_db
    ),
    user=Depends(
        get_current_user
    ),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    correction_id = await _commit_id_or_409(
        db,
        create_tax_invoice_correction_idempotent(
            db,
            company_id=company_id,
            request_key=payload.request_key,
            direction="output",
            original_tax_invoice_id=(
                payload.original_tax_invoice_id
            ),
            document_number=(
                payload.document_number
            ),
            document_date=(
                payload.document_date
            ),
            sources=[
                _source_line(
                    item
                )
                for item in payload.lines
            ],
            created_by=user.id,
        ),
    )

    return await _detail(
        db,
        company_id=company_id,
        tax_invoice_correction_id=(
            correction_id
        ),
    )


@router.post(
    "/input",
    response_model=TaxInvoiceCorrectionDetailResponse,
)
async def create_input_correction(
    company_id: int,
    payload: InputTaxInvoiceCorrectionCreate,
    db: AsyncSession = Depends(
        get_db
    ),
    user=Depends(
        get_current_user
    ),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    correction_id = await _commit_id_or_409(
        db,
        create_tax_invoice_correction_idempotent(
            db,
            company_id=company_id,
            request_key=payload.request_key,
            direction="input",
            original_tax_invoice_id=(
                payload.original_tax_invoice_id
            ),
            document_number=(
                payload.document_number
            ),
            document_date=(
                payload.document_date
            ),
            sources=[
                _source_line(
                    item
                )
                for item in payload.lines
            ],
            created_by=user.id,
            registration_date=(
                payload.registered_on
            ),
            registration_reference=(
                payload.receipt_reference
            ),
        ),
    )

    return await _detail(
        db,
        company_id=company_id,
        tax_invoice_correction_id=(
            correction_id
        ),
    )


@router.post(
    "/{tax_invoice_correction_id}/registration-events",
    response_model=TaxInvoiceCorrectionRegistrationResponse,
)
async def append_registration_event(
    company_id: int,
    tax_invoice_correction_id: int,
    payload: TaxInvoiceCorrectionRegistrationCreate,
    db: AsyncSession = Depends(
        get_db
    ),
    user=Depends(
        get_current_user
    ),
    _permission=Depends(
        require_company_permission(
            "journal_entries.approve"
        )
    ),
):
    event_id = await _commit_id_or_409(
        db,
        append_tax_invoice_correction_registration_idempotent(
            db,
            company_id=company_id,
            request_key=payload.request_key,
            tax_invoice_correction_id=(
                tax_invoice_correction_id
            ),
            registration_party=payload.registration_party,
            received_on=payload.received_on,
            status=payload.status,
            event_date=payload.event_date,
            reference=payload.reference,
            created_by=user.id,
        ),
    )

    event = await db.scalar(
        select(
            TaxInvoiceCorrectionRegistrationEvent
        ).where(
            TaxInvoiceCorrectionRegistrationEvent.company_id
            == company_id,
            TaxInvoiceCorrectionRegistrationEvent.id
            == event_id,
            TaxInvoiceCorrectionRegistrationEvent
            .tax_invoice_correction_id
            == tax_invoice_correction_id,
        )
    )

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Tax-invoice correction registration "
                "event not found after write"
            ),
        )

    return TaxInvoiceCorrectionRegistrationResponse.model_validate(
        event
    )


@router.get(
    "",
    response_model=list[
        TaxInvoiceCorrectionResponse
    ],
)
async def list_corrections(
    company_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    rows = list(
        (
            await db.scalars(
                select(
                    TaxInvoiceCorrection
                )
                .where(
                    TaxInvoiceCorrection.company_id
                    == company_id
                )
                .order_by(
                    TaxInvoiceCorrection.document_date.desc(),
                    TaxInvoiceCorrection.id.desc(),
                )
            )
        ).all()
    )

    return [
        TaxInvoiceCorrectionResponse.model_validate(
            row
        )
        for row in rows
    ]


@router.get(
    "/{tax_invoice_correction_id}",
    response_model=TaxInvoiceCorrectionDetailResponse,
)
async def get_correction(
    company_id: int,
    tax_invoice_correction_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "journal_entries.read"
        )
    ),
):
    return await _detail(
        db,
        company_id=company_id,
        tax_invoice_correction_id=(
            tax_invoice_correction_id
        ),
    )
