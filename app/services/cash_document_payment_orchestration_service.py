from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_document import CashDocument
from app.models.payment import Payment
from app.services.cash_document_service import (
    create_cash_document_evidence,
    create_cash_document_reentry_evidence,
    get_cash_document,
    reverse_cash_document_evidence,
)
from app.services.cash_document_types import (
    CashDocumentAlreadyReversedError,
    CashDocumentFacts,
    CashDocumentValidationError,
)
from app.services.idempotency_completion_service import (
    complete_idempotent_operation,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.idempotency_reservation_service import (
    reserve_idempotent_request,
)
from app.services.idempotency_request_validator import (
    IdempotencyKeyReuseError,
)
from app.services.payment_lifecycle_service import (
    cancel_payment,
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_types import PaymentDirection


CASH_DOCUMENT_PAYMENT_CREATE_OPERATION = (
    "cash_document_payment_create"
)
CASH_DOCUMENT_PAYMENT_RESULT_TYPE = "cash_document"


class CashDocumentPaymentOrchestrationError(Exception):
    pass


class CashDocumentPaymentActorError(
    CashDocumentPaymentOrchestrationError
):
    pass


class CashDocumentPaymentInProgressError(
    CashDocumentPaymentOrchestrationError
):
    pass


class CashDocumentPaymentIdempotencyConflictError(
    CashDocumentPaymentOrchestrationError
):
    pass


class CashDocumentPaymentFailedError(
    CashDocumentPaymentOrchestrationError
):
    pass


class CashDocumentPaymentResultError(
    CashDocumentPaymentOrchestrationError
):
    pass


@dataclass(frozen=True, slots=True)
class CashDocumentPaymentResult:
    payment: Payment
    cash_document: CashDocument


def _optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    value = value.strip()
    return value or None


def _canonical_create_payload(
    *,
    cash_desk_id: int,
    payment_number: str,
    document_number: str,
    direction: PaymentDirection,
    document_date: date,
    amount: Decimal,
    currency_code: str,
    counterparty_id: int,
    contract_id: int | None,
    external_reference: str | None,
    description: str | None,
    created_by: int,
) -> dict[str, Any]:
    direction_value = (
        direction.value
        if isinstance(direction, PaymentDirection)
        else str(direction)
    )

    return {
        "cash_desk_id": cash_desk_id,
        "payment_number": payment_number.strip(),
        "document_number": document_number.strip(),
        "direction": direction_value,
        "document_date": document_date.isoformat(),
        "amount": str(Decimal(amount)),
        "currency_code": currency_code.strip().upper(),
        "counterparty_id": counterparty_id,
        "contract_id": contract_id,
        "external_reference": _optional_text(
            external_reference
        ),
        "description": _optional_text(description),
        "created_by": created_by,
    }


async def _get_payment_for_document(
    db: AsyncSession,
    *,
    company_id: int,
    document: CashDocument,
) -> Payment:
    payment = (
        await db.execute(
            select(Payment).where(
                Payment.company_id == company_id,
                Payment.id == document.payment_id,
            )
        )
    ).scalar_one_or_none()

    if payment is None:
        raise CashDocumentPaymentResultError(
            "Stored CashDocument Payment result is missing"
        )

    return payment


async def create_confirm_and_record_cash_payment(
    db: AsyncSession,
    *,
    company_id: int,
    idempotency_key: str,
    cash_desk_id: int,
    payment_number: str,
    document_number: str,
    direction: PaymentDirection,
    document_date: date,
    amount: Decimal,
    currency_code: str,
    counterparty_id: int,
    contract_id: int | None,
    created_by: int,
    external_reference: str | None = None,
    description: str | None = None,
) -> CashDocumentPaymentResult:
    """
    Create canonical Payment + immutable CashDocument evidence.

    Payment creation and confirmation use the existing Payment
    lifecycle. CashDocument does not post GL directly.

    Caller owns COMMIT / ROLLBACK.
    """
    if created_by <= 0:
        raise CashDocumentPaymentActorError(
            "created_by must be greater than zero"
        )

    request_payload = _canonical_create_payload(
        cash_desk_id=cash_desk_id,
        payment_number=payment_number,
        document_number=document_number,
        direction=direction,
        document_date=document_date,
        amount=amount,
        currency_code=currency_code,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        external_reference=external_reference,
        description=description,
        created_by=created_by,
    )

    request_fingerprint = generate_request_fingerprint(
        request_payload
    )

    try:
        execution = await reserve_idempotent_request(
            session=db,
            company_id=company_id,
            operation=CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except IdempotencyKeyReuseError as exc:
        raise CashDocumentPaymentIdempotencyConflictError(
            str(exc)
        ) from exc

    if execution.decision == IdempotencyDecision.REUSE_RESULT:
        result = execution.reusable_result

        if (
            result is None
            or result.result_type
            != CASH_DOCUMENT_PAYMENT_RESULT_TYPE
        ):
            raise CashDocumentPaymentResultError(
                "Stored cash payment result is invalid"
            )

        try:
            cash_document_id = int(result.result_id)
        except (TypeError, ValueError) as exc:
            raise CashDocumentPaymentResultError(
                "Stored CashDocument result ID is invalid"
            ) from exc

        document = await get_cash_document(
            db,
            company_id=company_id,
            cash_document_id=cash_document_id,
        )

        if document.reversal_of_id is not None:
            raise CashDocumentPaymentResultError(
                "Stored result must reference an original "
                "CashDocument"
            )

        payment = await _get_payment_for_document(
            db,
            company_id=company_id,
            document=document,
        )

        return CashDocumentPaymentResult(
            payment=payment,
            cash_document=document,
        )

    if (
        execution.decision
        == IdempotencyDecision.ALREADY_IN_PROGRESS
    ):
        raise CashDocumentPaymentInProgressError(
            "Cash payment operation is already in progress"
        )

    if execution.decision != IdempotencyDecision.START_NEW:
        raise CashDocumentPaymentFailedError(
            "Cash payment operation cannot be started: "
            f"{execution.decision.value}"
        )

    payment = await create_payment_draft(
        db,
        company_id=company_id,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        number=payment_number,
        direction=direction,
        payment_date=document_date,
        currency_code=currency_code,
        amount=amount,
        created_by=created_by,
        external_reference=external_reference,
        description=description,
        cash_desk_id=cash_desk_id,
    )

    payment = await confirm_payment(
        db,
        company_id=company_id,
        payment_id=payment.id,
        confirmed_by=created_by,
    )

    facts = CashDocumentFacts(
        document_number=document_number,
        direction=direction,
        document_date=document_date,
        amount=Decimal(amount),
        currency_code=currency_code,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        external_reference=external_reference,
        description=description,
    )

    document = await create_cash_document_evidence(
        db,
        company_id=company_id,
        cash_desk_id=cash_desk_id,
        payment_id=payment.id,
        facts=facts,
        created_by=created_by,
    )

    await complete_idempotent_operation(
        session=db,
        company_id=company_id,
        operation=CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        result_type=CASH_DOCUMENT_PAYMENT_RESULT_TYPE,
        result_id=str(document.id),
        result_payload=None,
    )

    return CashDocumentPaymentResult(
        payment=payment,
        cash_document=document,
    )


async def cancel_and_reverse_cash_payment(
    db: AsyncSession,
    *,
    company_id: int,
    cash_document_id: int,
    reversal_document_number: str,
    created_by: int,
) -> CashDocumentPaymentResult:
    """
    Cancel the canonical Payment and append immutable
    CashDocument reversal evidence.

    No new Payment is created.
    Caller owns COMMIT / ROLLBACK.
    """
    if created_by <= 0:
        raise CashDocumentPaymentActorError(
            "created_by must be greater than zero"
        )

    original = await get_cash_document(
        db,
        company_id=company_id,
        cash_document_id=cash_document_id,
        for_update=True,
    )

    if original.reversal_of_id is not None:
        raise CashDocumentValidationError(
            "A reversal event cannot itself be reversed"
        )

    existing_reversal_id = (
        await db.execute(
            select(CashDocument.id).where(
                CashDocument.company_id == company_id,
                CashDocument.reversal_of_id == original.id,
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise CashDocumentAlreadyReversedError(
            "CashDocument has already been reversed"
        )

    payment = await cancel_payment(
        db,
        company_id=company_id,
        payment_id=original.payment_id,
        cancelled_by=created_by,
    )

    reversal = await reverse_cash_document_evidence(
        db,
        company_id=company_id,
        cash_document_id=original.id,
        reversal_document_number=reversal_document_number,
        created_by=created_by,
    )

    return CashDocumentPaymentResult(
        payment=payment,
        cash_document=reversal,
    )


async def create_cash_payment_reentry(
    db: AsyncSession,
    *,
    company_id: int,
    reversed_cash_document_id: int,
    cash_desk_id: int,
    payment_number: str,
    document_number: str,
    direction: PaymentDirection,
    document_date: date,
    amount: Decimal,
    currency_code: str,
    counterparty_id: int,
    contract_id: int | None,
    created_by: int,
    external_reference: str | None = None,
    description: str | None = None,
) -> CashDocumentPaymentResult:
    """
    Create a replacement after immutable reversal history exists.

    Re-entry always creates a new Payment and a new original
    CashDocument. Caller owns COMMIT / ROLLBACK.
    """
    if created_by <= 0:
        raise CashDocumentPaymentActorError(
            "created_by must be greater than zero"
        )

    original = await get_cash_document(
        db,
        company_id=company_id,
        cash_document_id=reversed_cash_document_id,
        for_update=True,
    )

    if original.reversal_of_id is not None:
        raise CashDocumentValidationError(
            "Re-entry must reference an original CashDocument"
        )

    # The evidence service is the canonical guard proving that
    # the original has already been reversed. We first create
    # the replacement Payment, then ask that service to append
    # the new immutable original. All work stays in one caller-
    # owned transaction, so failure rolls the new Payment back.
    payment = await create_payment_draft(
        db,
        company_id=company_id,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        number=payment_number,
        direction=direction,
        payment_date=document_date,
        currency_code=currency_code,
        amount=amount,
        created_by=created_by,
        external_reference=external_reference,
        description=description,
        cash_desk_id=cash_desk_id,
    )

    payment = await confirm_payment(
        db,
        company_id=company_id,
        payment_id=payment.id,
        confirmed_by=created_by,
    )

    facts = CashDocumentFacts(
        document_number=document_number,
        direction=direction,
        document_date=document_date,
        amount=Decimal(amount),
        currency_code=currency_code,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        external_reference=external_reference,
        description=description,
    )

    document = await create_cash_document_reentry_evidence(
        db,
        company_id=company_id,
        reversed_cash_document_id=original.id,
        cash_desk_id=cash_desk_id,
        payment_id=payment.id,
        facts=facts,
        created_by=created_by,
    )

    return CashDocumentPaymentResult(
        payment=payment,
        cash_document=document,
    )
