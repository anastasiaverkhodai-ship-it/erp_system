from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_statement_line import BankStatementLine
from app.models.bank_statement_reconciliation import (
    BankStatementReconciliation,
)
from app.models.payment import Payment

from app.services.bank_statement_reconciliation_service import (
    create_bank_statement_reconciliation,
    normalize_reconciliation_amount,
)
from app.services.bank_statement_service import (
    get_bank_statement_line,
)
from app.services.payment_lifecycle_service import (
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_types import PaymentDirection


class BankStatementPaymentOrchestrationError(Exception):
    pass


class BankStatementPaymentActorError(
    BankStatementPaymentOrchestrationError
):
    pass


@dataclass(frozen=True, slots=True)
class BankStatementPaymentResult:
    payment: Payment
    reconciliation: BankStatementReconciliation


def _direction_from_line(
    line: BankStatementLine,
) -> PaymentDirection:
    amount = Decimal(line.amount)

    if amount > 0:
        return PaymentDirection.INCOMING

    if amount < 0:
        return PaymentDirection.OUTGOING

    raise BankStatementPaymentOrchestrationError(
        "Bank statement line amount cannot be zero"
    )


def _full_line_amount(
    line: BankStatementLine,
) -> Decimal:
    return normalize_reconciliation_amount(
        amount=abs(Decimal(line.amount)),
        currency_code=line.currency_code,
    )


async def reconcile_bank_statement_line_to_payment(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
    payment_id: int,
    matched_amount: Decimal,
    created_by: int,
) -> BankStatementPaymentResult:
    """
    Match bank evidence to an existing Payment.

    The reconciliation domain owns validation of
    Payment status, direction, currency, amount,
    duplicate ACTIVE link and concrete BankAccount
    identity.

    Caller owns COMMIT / ROLLBACK.
    """
    if created_by <= 0:
        raise BankStatementPaymentActorError(
            "created_by must be greater than zero"
        )

    line = await get_bank_statement_line(
        db,
        company_id=company_id,
        bank_statement_line_id=(
            bank_statement_line_id
        ),
        lock_for_update=False,
    )

    reconciliation = (
        await create_bank_statement_reconciliation(
            db,
            company_id=company_id,
            bank_statement_line_id=line.id,
            payment_id=payment_id,
            matched_amount=matched_amount,
            currency_code=line.currency_code,
            created_by=created_by,
        )
    )

    # Reconciliation service has already locked and
    # validated the Payment. Read the resulting source
    # through the reconciliation FK without mutating it.
    from sqlalchemy import select

    payment = (
        await db.execute(
            select(Payment).where(
                Payment.company_id == company_id,
                Payment.id == payment_id,
            )
        )
    ).scalar_one()

    return BankStatementPaymentResult(
        payment=payment,
        reconciliation=reconciliation,
    )


async def create_confirm_and_reconcile_bank_statement_line(
    db: AsyncSession,
    *,
    company_id: int,
    bank_statement_line_id: int,
    counterparty_id: int,
    contract_id: int | None,
    number: str,
    created_by: int,
    external_reference: str | None = None,
    description: str | None = None,
) -> BankStatementPaymentResult:
    """
    Turn one immutable BankStatementLine into the
    canonical Payment operation, then reconcile it.

    Banking facts are derived from evidence and cannot
    be supplied independently by the caller:

      direction       <- line sign
      payment_date    <- line.transaction_date
      currency_code   <- line.currency_code
      amount          <- abs(line.amount)
      bank_account_id <- line.bank_account_id

    Payment creation, confirmation and reconciliation
    remain in one caller-owned transaction.
    """
    if created_by <= 0:
        raise BankStatementPaymentActorError(
            "created_by must be greater than zero"
        )

    line = await get_bank_statement_line(
        db,
        company_id=company_id,
        bank_statement_line_id=(
            bank_statement_line_id
        ),
        lock_for_update=True,
    )

    payment = await create_payment_draft(
        db,
        company_id=company_id,
        counterparty_id=counterparty_id,
        contract_id=contract_id,
        number=number,
        direction=_direction_from_line(line),
        payment_date=line.transaction_date,
        currency_code=line.currency_code,
        amount=_full_line_amount(line),
        created_by=created_by,
        external_reference=external_reference,
        description=description,
        bank_account_id=line.bank_account_id,
    )

    payment = await confirm_payment(
        db,
        company_id=company_id,
        payment_id=payment.id,
        confirmed_by=created_by,
    )

    reconciliation = (
        await create_bank_statement_reconciliation(
            db,
            company_id=company_id,
            bank_statement_line_id=line.id,
            payment_id=payment.id,
            matched_amount=_full_line_amount(line),
            currency_code=line.currency_code,
            created_by=created_by,
        )
    )

    return BankStatementPaymentResult(
        payment=payment,
        reconciliation=reconciliation,
    )
