from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.permissions import (
    require_company_permission,
)
from app.core.database import get_db
from app.api.deps import (
    get_current_user,
)
from app.models.payment import Payment
from app.models.user import User
from app.schemas.payment import (
    PaymentCreateRequest,
    PaymentResponse,
    PaymentSettlementAllocationCreateRequest,
    PaymentSettlementAllocationResponse,
    PaymentSettlementReconciliationResponse,
)
from app.services.payment_lifecycle_service import (
    PaymentActorError,
    PaymentAmountError,
    PaymentCompanyInvalidError,
    PaymentContractInvalidError,
    PaymentCounterpartyInvalidError,
    PaymentCurrencyError,
    PaymentDirectionError,
    PaymentLifecycleError,
    PaymentNotFoundError,
    PaymentNumberError,
    PaymentStatusError,
    cancel_payment,
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_settlement_service import (
    DuplicateActivePaymentSettlementError,
    OpenItemOverAllocationError,
    PaymentOverAllocationError,
    PaymentSettlementActorError,
    PaymentSettlementAmountError,
    PaymentSettlementContractError,
    PaymentSettlementCounterpartyError,
    PaymentSettlementCurrencyError,
    PaymentSettlementDataIntegrityError,
    PaymentSettlementDirectionError,
    PaymentSettlementError,
    PaymentSettlementNotFoundError,
    PaymentSettlementOpenItemStatusError,
    PaymentSettlementPaymentStatusError,
    PaymentSettlementReversalStateError,
    calculate_payment_unallocated_amount,
    create_payment_settlement_allocation,
    get_active_payment_settled_amounts,
    get_payment_settlement_allocation_history,
    get_payment_settlement_reconciliation,
    reverse_payment_settlement_allocation,
)


router = APIRouter(
    prefix="/companies/{company_id}/payments",
    tags=["Payments"],
)


def _payment_lifecycle_http_exception(
    exc: PaymentLifecycleError,
) -> HTTPException:
    if isinstance(
        exc,
        PaymentNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        PaymentStatusError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PaymentCompanyInvalidError,
            PaymentCounterpartyInvalidError,
            PaymentContractInvalidError,
            PaymentCurrencyError,
            PaymentAmountError,
            PaymentNumberError,
            PaymentDirectionError,
            PaymentActorError,
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


def _payment_settlement_http_exception(
    exc: PaymentSettlementError,
) -> HTTPException:
    if isinstance(
        exc,
        PaymentSettlementNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PaymentSettlementDirectionError,
            PaymentSettlementCounterpartyError,
            PaymentSettlementContractError,
            PaymentSettlementCurrencyError,
            PaymentSettlementAmountError,
            PaymentSettlementActorError,
        ),
    ):
        return HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        )

    if isinstance(
        exc,
        PaymentSettlementDataIntegrityError,
    ):
        return HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PaymentSettlementPaymentStatusError,
            PaymentSettlementOpenItemStatusError,
            PaymentOverAllocationError,
            OpenItemOverAllocationError,
            DuplicateActivePaymentSettlementError,
            PaymentSettlementReversalStateError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    )


def _payment_response(
    payment: Payment,
    *,
    settled_amount: Decimal,
) -> PaymentResponse:
    unallocated_amount = (
        calculate_payment_unallocated_amount(
            payment_amount=payment.amount,
            settled_amount=settled_amount,
        )
    )

    return PaymentResponse(
        id=payment.id,
        company_id=payment.company_id,
        counterparty_id=payment.counterparty_id,
        contract_id=payment.contract_id,
        number=payment.number,
        direction=payment.direction,
        status=payment.status,
        payment_date=payment.payment_date,
        currency_code=payment.currency_code,
        amount=payment.amount,
        settled_amount=settled_amount,
        unallocated_amount=(
            unallocated_amount
        ),
        external_reference=(
            payment.external_reference
        ),
        description=payment.description,
        created_by=payment.created_by,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        confirmed_at=payment.confirmed_at,
        cancelled_by=payment.cancelled_by,
        cancelled_at=payment.cancelled_at,
    )


@router.get(
    "",
    response_model=list[
        PaymentResponse
    ],
)
async def list_payments(
    company_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.read"
        )
    ),
):
    payments = list(
        (
            await db.execute(
                select(
                    Payment
                )
                .where(
                    Payment.company_id
                    == company_id
                )
                .order_by(
                    Payment.payment_date.desc(),
                    Payment.id.desc(),
                )
            )
        ).scalars().all()
    )

    settled = (
        await get_active_payment_settled_amounts(
            db,
            company_id=company_id,
            payment_ids=tuple(
                payment.id
                for payment in payments
            ),
        )
    )

    return [
        _payment_response(
            payment,
            settled_amount=settled.get(
                payment.id,
                Decimal("0"),
            ),
        )
        for payment in payments
    ]


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
)
async def get_payment(
    company_id: int,
    payment_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.read"
        )
    ),
):
    payment = (
        await db.execute(
            select(
                Payment
            ).where(
                Payment.company_id
                == company_id,
                Payment.id
                == payment_id,
            )
        )
    ).scalar_one_or_none()

    if payment is None:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="Payment not found",
        )

    settled = (
        await get_active_payment_settled_amounts(
            db,
            company_id=company_id,
            payment_ids=(
                payment.id,
            ),
        )
    )

    return _payment_response(
        payment,
        settled_amount=settled.get(
            payment.id,
            Decimal("0"),
        ),
    )


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=(
        status.HTTP_201_CREATED
    ),
)
async def create_payment(
    company_id: int,
    data: PaymentCreateRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
        )
    ),
):
    try:
        payment = (
            await create_payment_draft(
                db,
                company_id=company_id,
                counterparty_id=(
                    data.counterparty_id
                ),
                contract_id=(
                    data.contract_id
                ),
                number=data.number,
                direction=data.direction,
                payment_date=(
                    data.payment_date
                ),
                currency_code=(
                    data.currency_code
                ),
                amount=data.amount,
                created_by=current_user.id,
                external_reference=(
                    data.external_reference
                ),
                description=(
                    data.description
                ),
            )
        )

        await db.commit()

        await db.refresh(
            payment
        )

        return _payment_response(
            payment,
            settled_amount=Decimal("0"),
        )

    except PaymentLifecycleError as exc:
        await db.rollback()

        raise (
            _payment_lifecycle_http_exception(
                exc
            )
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Payment could not be created "
                "because of a data conflict"
            ),
        ) from exc


@router.post(
    "/{payment_id}/confirm",
    response_model=PaymentResponse,
)
async def confirm_payment_endpoint(
    company_id: int,
    payment_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
        )
    ),
):
    try:
        payment = (
            await confirm_payment(
                db,
                company_id=company_id,
                payment_id=payment_id,
            )
        )

        await db.commit()

        await db.refresh(
            payment
        )

        return _payment_response(
            payment,
            settled_amount=Decimal("0"),
        )

    except PaymentLifecycleError as exc:
        await db.rollback()

        raise (
            _payment_lifecycle_http_exception(
                exc
            )
        ) from exc


@router.post(
    "/{payment_id}/cancel",
    response_model=PaymentResponse,
)
async def cancel_payment_endpoint(
    company_id: int,
    payment_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.manage"
        )
    ),
):
    try:
        payment = (
            await cancel_payment(
                db,
                company_id=company_id,
                payment_id=payment_id,
                cancelled_by=(
                    current_user.id
                ),
            )
        )

        await db.commit()

        await db.refresh(
            payment
        )

        return _payment_response(
            payment,
            settled_amount=Decimal("0"),
        )

    except PaymentLifecycleError as exc:
        await db.rollback()

        raise (
            _payment_lifecycle_http_exception(
                exc
            )
        ) from exc


@router.post(
    "/{payment_id}/settlements",
    response_model=(
        PaymentSettlementAllocationResponse
    ),
)
async def create_payment_settlement(
    company_id: int,
    payment_id: int,
    data: (
        PaymentSettlementAllocationCreateRequest
    ),
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.settlements.manage"
        )
    ),
):
    try:
        allocation = (
            await create_payment_settlement_allocation(
                db,
                company_id=company_id,
                payment_id=payment_id,
                open_item_id=(
                    data.open_item_id
                ),
                amount=data.amount,
                created_by=current_user.id,
            )
        )

        await db.commit()

        return allocation

    except PaymentSettlementError as exc:
        await db.rollback()

        raise (
            _payment_settlement_http_exception(
                exc
            )
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Payment settlement could not "
                "be created because of a "
                "data conflict"
            ),
        ) from exc


@router.get(
    "/{payment_id}/settlements",
    response_model=list[
        PaymentSettlementAllocationResponse
    ],
)
async def list_payment_settlements(
    company_id: int,
    payment_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.settlements.read"
        )
    ),
):
    try:
        (
            _payment,
            allocations,
        ) = (
            await get_payment_settlement_allocation_history(
                db,
                company_id=company_id,
                payment_id=payment_id,
            )
        )

        return list(
            allocations
        )

    except PaymentSettlementError as exc:
        raise (
            _payment_settlement_http_exception(
                exc
            )
        ) from exc


@router.post(
    "/{payment_id}/settlements/"
    "{allocation_id}/reverse",
    response_model=(
        PaymentSettlementAllocationResponse
    ),
)
async def reverse_payment_settlement(
    company_id: int,
    payment_id: int,
    allocation_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.settlements.manage"
        )
    ),
):
    try:
        (
            payment,
            allocations,
        ) = (
            await get_payment_settlement_allocation_history(
                db,
                company_id=company_id,
                payment_id=payment_id,
            )
        )

        del payment

        if not any(
            allocation.id
            == allocation_id
            for allocation in allocations
        ):
            raise PaymentSettlementNotFoundError(
                "Payment settlement allocation "
                "not found for this Payment"
            )

        allocation = (
            await reverse_payment_settlement_allocation(
                db,
                company_id=company_id,
                allocation_id=allocation_id,
                reversed_by=current_user.id,
            )
        )

        await db.commit()

        return allocation

    except PaymentSettlementError as exc:
        await db.rollback()

        raise (
            _payment_settlement_http_exception(
                exc
            )
        ) from exc

    except IntegrityError as exc:
        await db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Payment settlement could not "
                "be reversed because of a "
                "data conflict"
            ),
        ) from exc


@router.get(
    "/{payment_id}/reconciliation",
    response_model=(
        PaymentSettlementReconciliationResponse
    ),
)
async def get_payment_reconciliation(
    company_id: int,
    payment_id: int,
    db: AsyncSession = Depends(
        get_db
    ),
    _permission=Depends(
        require_company_permission(
            "payments.settlements.read"
        )
    ),
):
    try:
        reconciliation = (
            await get_payment_settlement_reconciliation(
                db,
                company_id=company_id,
                payment_id=payment_id,
            )
        )

        payment = (
            reconciliation.payment
        )

        return (
            PaymentSettlementReconciliationResponse(
                company_id=payment.company_id,
                payment_id=payment.id,
                direction=payment.direction,
                status=payment.status,
                counterparty_id=(
                    payment.counterparty_id
                ),
                contract_id=(
                    payment.contract_id
                ),
                currency_code=(
                    payment.currency_code
                ),
                payment_amount=(
                    payment.amount
                ),
                settled_amount=(
                    reconciliation.settled_amount
                ),
                unallocated_amount=(
                    reconciliation.unallocated_amount
                ),
                fully_allocated=(
                    reconciliation.fully_allocated
                ),
                allocations=list(
                    reconciliation.allocations
                ),
            )
        )

    except PaymentSettlementError as exc:
        raise (
            _payment_settlement_http_exception(
                exc
            )
        ) from exc
