from fastapi import (
    APIRouter,
    Depends,
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
from app.schemas.bank_statement_payment import (
    BankStatementCreatePaymentRequest,
    BankStatementExistingPaymentMatchRequest,
    BankStatementPaymentMatchResponse,
)
from app.services.bank_statement_payment_orchestration_service import (
    BankStatementPaymentOrchestrationError,
    create_confirm_and_reconcile_bank_statement_line,
    reconcile_bank_statement_line_to_payment,
)
from app.services.bank_statement_reconciliation_service import (
    BankStatementReconciliationError,
)
from app.services.bank_statement_service import (
    BankStatementNotFoundError,
)
from app.services.payment_lifecycle_service import (
    PaymentLifecycleError,
    PaymentNotFoundError,
    PaymentStatusError,
)


router = APIRouter(
    prefix="/companies/{company_id}/bank-statements",
    tags=["Bank Statements"],
)


def _domain_http_error(
    exc: Exception,
) -> HTTPException:
    if isinstance(
        exc,
        (
            BankStatementNotFoundError,
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
            PaymentStatusError,
            BankStatementReconciliationError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            PaymentLifecycleError,
            BankStatementPaymentOrchestrationError,
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


def _response(
    *,
    payment_id: int,
    bank_account_id: int | None,
    reconciliation_id: int,
    bank_statement_line_id: int,
    matched_amount,
    currency_code: str,
) -> BankStatementPaymentMatchResponse:
    return BankStatementPaymentMatchResponse(
        payment_id=payment_id,
        bank_account_id=bank_account_id,
        reconciliation_id=reconciliation_id,
        bank_statement_line_id=(
            bank_statement_line_id
        ),
        matched_amount=matched_amount,
        currency_code=currency_code,
    )


@router.post(
    "/lines/{bank_statement_line_id}/reconcile-existing",
    response_model=BankStatementPaymentMatchResponse,
)
async def reconcile_existing_payment(
    company_id: int,
    bank_statement_line_id: int,
    data: BankStatementExistingPaymentMatchRequest,
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
        result = (
            await reconcile_bank_statement_line_to_payment(
                db,
                company_id=company_id,
                bank_statement_line_id=(
                    bank_statement_line_id
                ),
                payment_id=data.payment_id,
                matched_amount=data.matched_amount,
                created_by=current_user.id,
            )
        )

        response = _response(
            payment_id=result.payment.id,
            bank_account_id=(
                result.payment.bank_account_id
            ),
            reconciliation_id=(
                result.reconciliation.id
            ),
            bank_statement_line_id=(
                result.reconciliation
                .bank_statement_line_id
            ),
            matched_amount=(
                result.reconciliation.matched_amount
            ),
            currency_code=(
                result.reconciliation.currency_code
            ),
        )

        await db.commit()
        return response

    except (
        BankStatementNotFoundError,
        BankStatementPaymentOrchestrationError,
        BankStatementReconciliationError,
        PaymentLifecycleError,
    ) as exc:
        await db.rollback()
        raise _domain_http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bank reconciliation data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise


@router.post(
    "/lines/{bank_statement_line_id}/create-payment",
    response_model=BankStatementPaymentMatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_from_bank_line(
    company_id: int,
    bank_statement_line_id: int,
    data: BankStatementCreatePaymentRequest,
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
        result = (
            await create_confirm_and_reconcile_bank_statement_line(
                db,
                company_id=company_id,
                bank_statement_line_id=(
                    bank_statement_line_id
                ),
                counterparty_id=(
                    data.counterparty_id
                ),
                contract_id=data.contract_id,
                number=data.number,
                created_by=current_user.id,
                external_reference=(
                    data.external_reference
                ),
                description=data.description,
            )
        )

        response = _response(
            payment_id=result.payment.id,
            bank_account_id=(
                result.payment.bank_account_id
            ),
            reconciliation_id=(
                result.reconciliation.id
            ),
            bank_statement_line_id=(
                result.reconciliation
                .bank_statement_line_id
            ),
            matched_amount=(
                result.reconciliation.matched_amount
            ),
            currency_code=(
                result.reconciliation.currency_code
            ),
        )

        await db.commit()
        return response

    except (
        BankStatementNotFoundError,
        BankStatementPaymentOrchestrationError,
        BankStatementReconciliationError,
        PaymentLifecycleError,
    ) as exc:
        await db.rollback()
        raise _domain_http_error(exc) from exc

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bank payment orchestration data conflict",
        ) from exc

    except Exception:
        await db.rollback()
        raise
