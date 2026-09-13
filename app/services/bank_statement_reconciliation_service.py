from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_statement_line import BankStatementLine
from app.models.bank_statement_reconciliation import (
    BankStatementReconciliation,
    BankStatementReconciliationActiveLink,
)
from app.models.payment import Payment
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


ZERO = Decimal("0")


class BankStatementReconciliationError(Exception):
    pass


class BankStatementReconciliationNotFoundError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationLineError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationPaymentError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationCurrencyError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationBankAccountError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationDirectionError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationAmountError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationDuplicateError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationActorError(
    BankStatementReconciliationError
):
    pass


class BankStatementReconciliationReversalError(
    BankStatementReconciliationError
):
    pass


def normalize_reconciliation_currency(
    currency_code: str,
) -> str:
    normalized = currency_code.strip().upper()

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise BankStatementReconciliationCurrencyError(
            "Reconciliation currency code must contain "
            "exactly three letters"
        )

    return normalized


def normalize_reconciliation_amount(
    *,
    amount: Decimal,
    currency_code: str,
) -> Decimal:
    normalized = round_currency_amount(
        amount=Decimal(str(amount)),
        currency_code=currency_code,
    )

    if normalized <= ZERO:
        raise BankStatementReconciliationAmountError(
            "Reconciliation amount must be greater than zero"
        )

    return normalized


def expected_payment_direction(
    line: BankStatementLine,
) -> PaymentDirection:
    amount = Decimal(line.amount)

    if amount > ZERO:
        return PaymentDirection.INCOMING

    if amount < ZERO:
        return PaymentDirection.OUTGOING

    raise BankStatementReconciliationLineError(
        "Bank statement line amount cannot be zero"
    )


async def create_bank_statement_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
    payment_id: int,
    matched_amount: Decimal,
    currency_code: str,
    created_by: int,
) -> BankStatementReconciliation:
    """
    Create one immutable original reconciliation event and
    its mutable ACTIVE projection.

    Caller owns COMMIT / ROLLBACK.

    This service does not:
      - create Payment
      - confirm Payment
      - mutate Payment
      - settle Payment
      - create GL
    """

    if created_by <= 0:
        raise BankStatementReconciliationActorError(
            "created_by must be greater than zero"
        )

    line = (
        await db.execute(
            select(
                BankStatementLine
            )
            .where(
                BankStatementLine.company_id
                == company_id,
                BankStatementLine.id
                == bank_statement_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise BankStatementReconciliationLineError(
            "Bank statement line was not found for company"
        )

    payment = (
        await db.execute(
            select(
                Payment
            )
            .where(
                Payment.company_id == company_id,
                Payment.id == payment_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if payment is None:
        raise BankStatementReconciliationPaymentError(
            "Payment was not found for company"
        )

    if payment.status != PaymentStatus.CONFIRMED:
        raise BankStatementReconciliationPaymentError(
            "Only confirmed Payment can be reconciled"
        )

    if (
        payment.bank_account_id is not None
        and payment.bank_account_id
        != line.bank_account_id
    ):
        raise BankStatementReconciliationBankAccountError(
            "Bank statement line and Payment "
            "bank account must match"
        )

    line_currency = (
        normalize_reconciliation_currency(
            line.currency_code
        )
    )

    payment_currency = (
        normalize_reconciliation_currency(
            payment.currency_code
        )
    )

    requested_currency = (
        normalize_reconciliation_currency(
            currency_code
        )
    )

    if not (
        line_currency
        == payment_currency
        == requested_currency
    ):
        raise BankStatementReconciliationCurrencyError(
            "Bank statement line, Payment and "
            "reconciliation currency must match"
        )

    direction = expected_payment_direction(
        line
    )

    if payment.direction != direction:
        raise BankStatementReconciliationDirectionError(
            "Bank statement line sign does not match "
            "Payment direction"
        )

    normalized_amount = (
        normalize_reconciliation_amount(
            amount=matched_amount,
            currency_code=requested_currency,
        )
    )

    line_amount = abs(
        round_currency_amount(
            amount=Decimal(line.amount),
            currency_code=line_currency,
        )
    )

    payment_amount = round_currency_amount(
        amount=Decimal(payment.amount),
        currency_code=payment_currency,
    )

    active_line = (
        await db.execute(
            select(
                BankStatementReconciliationActiveLink
            )
            .where(
                BankStatementReconciliationActiveLink.company_id
                == company_id,
                BankStatementReconciliationActiveLink.bank_statement_line_id
                == bank_statement_line_id,
            )
        )
    ).scalar_one_or_none()

    if active_line is not None:
        raise BankStatementReconciliationDuplicateError(
            "Bank statement line already has "
            "an active reconciliation"
        )

    if normalized_amount > line_amount:
        raise BankStatementReconciliationAmountError(
            "Reconciliation amount exceeds "
            "BankStatementLine amount"
        )

    active_payment_amount = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        BankStatementReconciliation.matched_amount
                    ),
                    ZERO,
                )
            )
            .select_from(
                BankStatementReconciliationActiveLink
            )
            .join(
                BankStatementReconciliation,
                (
                    BankStatementReconciliation.company_id
                    == BankStatementReconciliationActiveLink.company_id
                )
                & (
                    BankStatementReconciliation.id
                    == BankStatementReconciliationActiveLink.reconciliation_id
                ),
            )
            .where(
                BankStatementReconciliationActiveLink.company_id
                == company_id,
                BankStatementReconciliationActiveLink.payment_id
                == payment_id,
            )
        )
    ).scalar_one()

    if (
        Decimal(active_payment_amount)
        + normalized_amount
        > payment_amount
    ):
        raise BankStatementReconciliationAmountError(
            "ACTIVE bank reconciliations exceed Payment amount"
        )

    reconciliation = BankStatementReconciliation(
        company_id=company_id,
        bank_statement_line_id=bank_statement_line_id,
        payment_id=payment_id,
        matched_amount=normalized_amount,
        currency_code=requested_currency,
        created_by=created_by,
        reversal_of_id=None,
    )

    db.add(reconciliation)
    await db.flush()

    active_link = (
        BankStatementReconciliationActiveLink(
            company_id=company_id,
            bank_statement_line_id=bank_statement_line_id,
            payment_id=payment_id,
            reconciliation_id=reconciliation.id,
        )
    )

    db.add(active_link)
    await db.flush()

    return reconciliation


async def reverse_bank_statement_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    reconciliation_id: int,
    reversed_by: int,
) -> BankStatementReconciliation:
    """
    Unmatch one currently ACTIVE reconciliation.

    Immutable history:
      - original event is not updated
      - reversal is a new event
      - current-state projection row is deleted

    Caller owns COMMIT / ROLLBACK.
    """

    if reversed_by <= 0:
        raise BankStatementReconciliationActorError(
            "reversed_by must be greater than zero"
        )

    original = (
        await db.execute(
            select(
                BankStatementReconciliation
            )
            .where(
                BankStatementReconciliation.company_id
                == company_id,
                BankStatementReconciliation.id
                == reconciliation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if original is None:
        raise BankStatementReconciliationNotFoundError(
            "Bank statement reconciliation was not found"
        )

    if original.reversal_of_id is not None:
        raise BankStatementReconciliationReversalError(
            "A reversal event cannot itself be reversed"
        )

    active_link = (
        await db.execute(
            select(
                BankStatementReconciliationActiveLink
            )
            .where(
                BankStatementReconciliationActiveLink.company_id
                == company_id,
                BankStatementReconciliationActiveLink.reconciliation_id
                == reconciliation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if active_link is None:
        existing_reversal = (
            await db.execute(
                select(
                    BankStatementReconciliation.id
                ).where(
                    BankStatementReconciliation.reversal_of_id
                    == reconciliation_id
                )
            )
        ).scalar_one_or_none()

        if existing_reversal is not None:
            raise BankStatementReconciliationReversalError(
                "Bank statement reconciliation "
                "was already reversed"
            )

        raise BankStatementReconciliationReversalError(
            "Bank statement reconciliation "
            "is not currently active"
        )

    reversal = BankStatementReconciliation(
        company_id=original.company_id,
        bank_statement_line_id=(
            original.bank_statement_line_id
        ),
        payment_id=original.payment_id,
        matched_amount=original.matched_amount,
        currency_code=original.currency_code,
        created_by=reversed_by,
        reversal_of_id=original.id,
    )

    db.add(reversal)
    await db.flush()

    await db.execute(
        delete(
            BankStatementReconciliationActiveLink
        ).where(
            BankStatementReconciliationActiveLink.id
            == active_link.id
        )
    )

    await db.flush()

    return reversal


async def get_bank_statement_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    reconciliation_id: int,
) -> BankStatementReconciliation:
    reconciliation = (
        await db.execute(
            select(
                BankStatementReconciliation
            ).where(
                BankStatementReconciliation.company_id
                == company_id,
                BankStatementReconciliation.id
                == reconciliation_id,
            )
        )
    ).scalar_one_or_none()

    if reconciliation is None:
        raise BankStatementReconciliationNotFoundError(
            "Bank statement reconciliation was not found"
        )

    return reconciliation


async def get_active_bank_statement_line_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
) -> BankStatementReconciliation | None:
    active_link = (
        await db.execute(
            select(
                BankStatementReconciliationActiveLink
            ).where(
                BankStatementReconciliationActiveLink.company_id
                == company_id,
                BankStatementReconciliationActiveLink.bank_statement_line_id
                == bank_statement_line_id,
            )
        )
    ).scalar_one_or_none()

    if active_link is None:
        return None

    return (
        await db.execute(
            select(
                BankStatementReconciliation
            ).where(
                BankStatementReconciliation.company_id
                == company_id,
                BankStatementReconciliation.id
                == active_link.reconciliation_id,
            )
        )
    ).scalar_one()


async def list_bank_statement_line_reconciliations(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
) -> list[BankStatementReconciliation]:
    result = await db.execute(
        select(
            BankStatementReconciliation
        )
        .where(
            BankStatementReconciliation.company_id
            == company_id,
            BankStatementReconciliation.bank_statement_line_id
            == bank_statement_line_id,
        )
        .order_by(
            BankStatementReconciliation.id
        )
    )

    return list(
        result.scalars().all()
    )


async def list_payment_bank_reconciliations(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> list[BankStatementReconciliation]:
    result = await db.execute(
        select(
            BankStatementReconciliation
        )
        .where(
            BankStatementReconciliation.company_id
            == company_id,
            BankStatementReconciliation.payment_id
            == payment_id,
        )
        .order_by(
            BankStatementReconciliation.id
        )
    )

    return list(
        result.scalars().all()
    )
