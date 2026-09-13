from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.models.user import User
from app.schemas.cash_document_payment import (
    CashDocumentPaymentCreateRequest,
    CashDocumentPaymentResponse,
    CashDocumentPaymentReverseRequest,
)
from app.services.cash_document_payment_orchestration_service import (
    CashDocumentPaymentFailedError,
    CashDocumentPaymentIdempotencyConflictError,
    CashDocumentPaymentInProgressError,
    CashDocumentPaymentOrchestrationError,
    CashDocumentPaymentResult,
    cancel_and_reverse_cash_payment,
    create_cash_payment_reentry,
    create_confirm_and_record_cash_payment,
)
from app.services.cash_document_types import (
    CashDocumentAlreadyReversedError,
    CashDocumentError,
    CashDocumentNotFoundError,
)
from app.services.payment_lifecycle_service import (
    PaymentLifecycleError,
    PaymentNotFoundError,
    PaymentStatusError,
)


router = APIRouter(
    prefix="/companies/{company_id}/cash-documents",
    tags=["Cash Documents"],
)


def _response(
    result: CashDocumentPaymentResult,
) -> CashDocumentPaymentResponse:
    payment = result.payment
    document = result.cash_document

    return CashDocumentPaymentResponse(
        payment_id=payment.id,
        payment_status=payment.status,
        cash_document_id=document.id,
        cash_desk_id=document.cash_desk_id,
        document_number=document.document_number,
        direction=document.direction,
        document_date=document.document_date,
        amount=document.amount,
        currency_code=document.currency_code,
        reversal_of_id=document.reversal_of_id,
    )


def _domain_http_error(
    exc: Exception,
) -> HTTPException:
    if isinstance(
        exc,
        (
            CashDocumentNotFoundError,
            PaymentNotFoundError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            CashDocumentAlreadyReversedError,
            PaymentStatusError,
            CashDocumentPaymentIdempotencyConflictError,
            CashDocumentPaymentInProgressError,
            CashDocumentPaymentFailedError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            CashDocumentError,
            PaymentLifecycleError,
            CashDocumentPaymentOrchestrationError,
        ),
    ):
        return HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    )


@router.post(
    "",
    response_model=CashDocumentPaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cash_payment(
    company_id: int,
    data: CashDocumentPaymentCreateRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
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
        result = (
            await create_confirm_and_record_cash_payment(
                db,
                company_id=company_id,
                idempotency_key=idempotency_key,
                cash_desk_id=data.cash_desk_id,
                payment_number=data.payment_number,
                document_number=data.document_number,
                direction=data.direction,
                document_date=data.document_date,
                amount=data.amount,
                currency_code=data.currency_code,
                counterparty_id=data.counterparty_id,
                contract_id=data.contract_id,
                created_by=current_user.id,
                external_reference=(
                    data.external_reference
                ),
                description=data.description,
            )
        )

        response = _response(result)

        await db.commit()

        return response

    except (
        CashDocumentError,
        PaymentLifecycleError,
        CashDocumentPaymentOrchestrationError,
    ) as exc:
        await db.rollback()
        raise _domain_http_error(exc) from exc

    except ValueError as exc:
        # Idempotency request fingerprint mismatch and other
        # domain validation failures fail closed.
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
                "Cash payment orchestration data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{cash_document_id}/reverse",
    response_model=CashDocumentPaymentResponse,
)
async def reverse_cash_payment(
    company_id: int,
    cash_document_id: int,
    data: CashDocumentPaymentReverseRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
        )
    ),
):
    try:
        result = await cancel_and_reverse_cash_payment(
            db,
            company_id=company_id,
            cash_document_id=cash_document_id,
            reversal_document_number=(
                data.reversal_document_number
            ),
            created_by=current_user.id,
        )

        response = _response(result)

        await db.commit()

        return response

    except (
        CashDocumentError,
        PaymentLifecycleError,
        CashDocumentPaymentOrchestrationError,
    ) as exc:
        await db.rollback()
        raise _domain_http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cash reversal orchestration data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/{cash_document_id}/reentry",
    response_model=CashDocumentPaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reenter_cash_payment(
    company_id: int,
    cash_document_id: int,
    data: CashDocumentPaymentCreateRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(get_db),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
        )
    ),
):
    try:
        result = await create_cash_payment_reentry(
            db,
            company_id=company_id,
            reversed_cash_document_id=(
                cash_document_id
            ),
            cash_desk_id=data.cash_desk_id,
            payment_number=data.payment_number,
            document_number=data.document_number,
            direction=data.direction,
            document_date=data.document_date,
            amount=data.amount,
            currency_code=data.currency_code,
            counterparty_id=data.counterparty_id,
            contract_id=data.contract_id,
            created_by=current_user.id,
            external_reference=(
                data.external_reference
            ),
            description=data.description,
        )

        response = _response(result)

        await db.commit()

        return response

    except (
        CashDocumentError,
        PaymentLifecycleError,
        CashDocumentPaymentOrchestrationError,
    ) as exc:
        await db.rollback()
        raise _domain_http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cash re-entry orchestration data conflict"
            ),
        ) from exc

    except Exception:
        await db.rollback()
        raise
