from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contract import Contract
from app.models.counterparty import Counterparty
from app.models.payment import Payment
from app.services.contract_types import ContractStatus
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)
from app.services.payment_journal_service import (
    PaymentJournalCurrencyError,
    PaymentJournalError,
    generate_and_post_payment_journal_entry,
    reverse_payment_journal_entry,
)

from app.services.payment_settlement_service import (
    has_active_payment_settlement_allocations,
)


ZERO = Decimal("0")


class PaymentLifecycleError(Exception):
    pass


class PaymentNotFoundError(
    PaymentLifecycleError
):
    pass


class PaymentStatusError(
    PaymentLifecycleError
):
    pass


class PaymentCompanyInvalidError(
    PaymentLifecycleError
):
    pass


class PaymentCounterpartyInvalidError(
    PaymentLifecycleError
):
    pass


class PaymentContractInvalidError(
    PaymentLifecycleError
):
    pass


class PaymentCurrencyError(
    PaymentLifecycleError
):
    pass


class PaymentAmountError(
    PaymentLifecycleError
):
    pass


class PaymentNumberError(
    PaymentLifecycleError
):
    pass


class PaymentDirectionError(
    PaymentLifecycleError
):
    pass


class PaymentActorError(
    PaymentLifecycleError
):
    pass


def normalize_payment_currency_code(
    currency_code: str,
) -> str:
    normalized = (
        currency_code.strip().upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise PaymentCurrencyError(
            "Payment currency code must contain "
            "exactly three letters"
        )

    return normalized


def normalize_payment_number(
    number: str,
) -> str:
    normalized = number.strip()

    if not normalized:
        raise PaymentNumberError(
            "Payment number cannot be empty"
        )

    if len(normalized) > 100:
        raise PaymentNumberError(
            "Payment number cannot exceed 100 characters"
        )

    return normalized


def normalize_payment_amount(
    amount: Decimal,
) -> Decimal:
    normalized = Decimal(
        str(amount)
    )

    if normalized <= ZERO:
        raise PaymentAmountError(
            "Payment amount must be greater than zero"
        )

    return normalized


def normalize_payment_direction(
    direction: PaymentDirection,
) -> PaymentDirection:
    try:
        return PaymentDirection(
            direction
        )
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise PaymentDirectionError(
            "Unsupported payment direction"
        ) from exc


def validate_payment_confirmation(
    payment: Payment,
) -> None:
    """
    Validate persistent Payment state immediately
    before confirmation.

    STEP 16A confirmation is commercial only:
      - no JournalEntry
      - no stock
      - no tax recognition
      - no settlement allocation
    """

    if (
        payment.status
        != PaymentStatus.DRAFT
    ):
        raise PaymentStatusError(
            "Only draft Payments can be confirmed"
        )

    normalize_payment_number(
        payment.number
    )

    normalize_payment_direction(
        payment.direction
    )

    normalize_payment_currency_code(
        payment.currency_code
    )

    normalize_payment_amount(
        payment.amount
    )


def validate_payment_cancellation(
    payment: Payment,
) -> None:
    """
    DRAFT or CONFIRMED Payment may be cancelled.

    STEP 16.2.3 will add the ACTIVE settlement
    allocation guard for CONFIRMED Payments.
    """

    if payment.status not in (
        PaymentStatus.DRAFT,
        PaymentStatus.CONFIRMED,
    ):
        raise PaymentStatusError(
            "Only draft or confirmed Payments "
            "can be cancelled"
        )


async def revalidate_payment_references(
    db: AsyncSession,
    *,
    payment: Payment,
) -> None:
    """
    Revalidate mutable business references.

    Rules:
      - company must still be active
      - counterparty must still be active
        and company-scoped
      - optional contract must be ACTIVE,
        belong to the same company/counterparty
      - if a contract exists, Payment currency
        must equal Contract currency

    Payment direction deliberately does NOT restrict
    CounterpartyType or ContractType. Incoming/outgoing
    refunds and advances are legitimate business cases.
    """

    company_exists = (
        await db.execute(
            select(
                Company.id
            ).where(
                Company.id
                == payment.company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company_exists is None:
        raise PaymentCompanyInvalidError(
            "Payment company is missing or inactive"
        )

    counterparty_exists = (
        await db.execute(
            select(
                Counterparty.id
            ).where(
                Counterparty.id
                == payment.counterparty_id,
                Counterparty.company_id
                == payment.company_id,
                Counterparty.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if counterparty_exists is None:
        raise PaymentCounterpartyInvalidError(
            "Payment counterparty is missing, inactive, "
            "or belongs to another company"
        )

    if payment.contract_id is None:
        return

    contract = (
        await db.execute(
            select(
                Contract
            ).where(
                Contract.id
                == payment.contract_id,
                Contract.company_id
                == payment.company_id,
                Contract.counterparty_id
                == payment.counterparty_id,
                Contract.status
                == ContractStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()

    if contract is None:
        raise PaymentContractInvalidError(
            "Payment contract is missing, inactive, "
            "or does not belong to the same "
            "company/counterparty"
        )

    payment_currency = (
        normalize_payment_currency_code(
            payment.currency_code
        )
    )

    contract_currency = (
        normalize_payment_currency_code(
            contract.currency_code
        )
    )

    if (
        payment_currency
        != contract_currency
    ):
        raise PaymentCurrencyError(
            "Payment currency does not match "
            "Contract currency"
        )


async def create_payment_draft(
    db: AsyncSession,
    *,
    company_id: int,
    counterparty_id: int,
    contract_id: int | None,
    number: str,
    direction: PaymentDirection,
    payment_date: date,
    currency_code: str,
    amount: Decimal,
    created_by: int,
    external_reference: str | None = None,
    description: str | None = None,
) -> Payment:
    """
    Create one DRAFT Payment.

    Caller owns COMMIT / ROLLBACK.
    """

    if created_by <= 0:
        raise PaymentActorError(
            "created_by must be greater than zero"
        )

    payment = Payment(
        company_id=company_id,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        number=normalize_payment_number(
            number
        ),
        direction=normalize_payment_direction(
            direction
        ),
        status=PaymentStatus.DRAFT,
        payment_date=payment_date,
        currency_code=(
            normalize_payment_currency_code(
                currency_code
            )
        ),
        amount=normalize_payment_amount(
            amount
        ),
        external_reference=(
            external_reference
        ),
        description=description,
        created_by=created_by,
    )

    await revalidate_payment_references(
        db,
        payment=payment,
    )

    db.add(
        payment
    )

    await db.flush()

    return payment


async def get_locked_payment(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> Payment:
    """
    Lock one Payment header for lifecycle mutation.
    """

    payment = (
        await db.execute(
            select(
                Payment
            )
            .where(
                Payment.id
                == payment_id,
                Payment.company_id
                == company_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if payment is None:
        raise PaymentNotFoundError(
            "Payment not found"
        )

    return payment


async def confirm_payment(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    confirmed_by: int,
) -> Payment:
    """
    Confirm one Payment atomically.

    Payment status and GL posting belong to the
    same caller-owned transaction.
    """

    if confirmed_by <= 0:
        raise PaymentActorError(
            "confirmed_by must be greater than zero"
        )

    payment = await get_locked_payment(
        db,
        company_id=company_id,
        payment_id=payment_id,
    )

    validate_payment_confirmation(
        payment
    )

    payment.number = (
        normalize_payment_number(
            payment.number
        )
    )

    payment.direction = (
        normalize_payment_direction(
            payment.direction
        )
    )

    payment.currency_code = (
        normalize_payment_currency_code(
            payment.currency_code
        )
    )

    payment.amount = (
        normalize_payment_amount(
            payment.amount
        )
    )

    await revalidate_payment_references(
        db,
        payment=payment,
    )

    payment.status = (
        PaymentStatus.CONFIRMED
    )

    payment.confirmed_at = (
        datetime.now(
            timezone.utc
        )
    )

    await db.flush()

    try:
        await generate_and_post_payment_journal_entry(
            db,
            payment=payment,
            created_by=confirmed_by,
        )
    except PaymentJournalCurrencyError as exc:
        raise PaymentCurrencyError(
            str(exc)
        ) from exc
    except PaymentJournalError as exc:
        raise PaymentStatusError(
            "Payment accounting failed: "
            f"{exc}"
        ) from exc

    return payment


async def cancel_payment(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    cancelled_by: int,
) -> Payment:
    """
    Cancel one DRAFT or CONFIRMED Payment.

    DRAFT cancellation has no GL effect.

    CONFIRMED cancellation reverses its Payment
    JournalEntry in the same caller-owned transaction.
    """

    if cancelled_by <= 0:
        raise PaymentActorError(
            "cancelled_by must be greater than zero"
        )

    payment = await get_locked_payment(
        db,
        company_id=company_id,
        payment_id=payment_id,
    )

    validate_payment_cancellation(
        payment
    )

    was_confirmed = (
        payment.status
        == PaymentStatus.CONFIRMED
    )

    if await has_active_payment_settlement_allocations(
        db,
        company_id=company_id,
        payment_id=payment.id,
        lock_rows=True,
    ):
        raise PaymentStatusError(
            "Payment has ACTIVE settlement "
            "allocations; reverse them before "
            "cancellation"
        )

    cancellation_time = datetime.now(
        timezone.utc
    )

    if was_confirmed:
        try:
            await reverse_payment_journal_entry(
                db,
                company_id=company_id,
                payment_id=payment.id,
                reversal_date=(
                    cancellation_time.date()
                ),
                reversed_by=cancelled_by,
            )
        except PaymentJournalError as exc:
            raise PaymentStatusError(
                "Payment accounting reversal "
                f"failed: {exc}"
            ) from exc

    payment.status = (
        PaymentStatus.CANCELLED
    )

    payment.cancelled_by = (
        cancelled_by
    )

    payment.cancelled_at = (
        cancellation_time
    )

    await db.flush()

    return payment
