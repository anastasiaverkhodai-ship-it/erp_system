from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_desk import CashDesk
from app.models.cash_document import CashDocument
from app.models.payment import Payment
from app.services.cash_document_types import (
    CashDocumentAlreadyReversedError,
    CashDocumentFacts,
    CashDocumentNotFoundError,
    CashDocumentValidationError,
)


def _normalize_currency(value: str) -> str:
    value = value.strip().upper()

    if len(value) != 3:
        raise CashDocumentValidationError(
            "CashDocument currency must contain 3 characters"
        )

    return value


def _normalize_number(value: str) -> str:
    value = value.strip()

    if not value:
        raise CashDocumentValidationError(
            "CashDocument number cannot be blank"
        )

    return value


def _normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    value = value.strip()

    return value or None


async def get_cash_document(
    db: AsyncSession,
    *,
    company_id: int,
    cash_document_id: int,
    for_update: bool = False,
) -> CashDocument:
    stmt = select(CashDocument).where(
        CashDocument.id == cash_document_id,
        CashDocument.company_id == company_id,
    )

    if for_update:
        stmt = stmt.with_for_update()

    document = (
        await db.execute(stmt)
    ).scalar_one_or_none()

    if document is None:
        raise CashDocumentNotFoundError(
            "CashDocument not found"
        )

    return document


async def _get_cash_desk(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
) -> CashDesk:
    cash_desk = (
        await db.execute(
            select(CashDesk).where(
                CashDesk.id == cash_desk_id,
                CashDesk.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if cash_desk is None:
        raise CashDocumentValidationError(
            "CashDesk not found in company"
        )

    return cash_desk


async def _get_payment(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    for_update: bool = False,
) -> Payment:
    stmt = select(Payment).where(
        Payment.id == payment_id,
        Payment.company_id == company_id,
    )

    if for_update:
        stmt = stmt.with_for_update()

    payment = (
        await db.execute(stmt)
    ).scalar_one_or_none()

    if payment is None:
        raise CashDocumentValidationError(
            "Payment not found in company"
        )

    return payment


def _validate_document_payment_identity(
    *,
    cash_desk: CashDesk,
    payment: Payment,
    facts: CashDocumentFacts,
) -> tuple[str, Decimal]:
    currency = _normalize_currency(
        facts.currency_code
    )
    amount = Decimal(facts.amount)

    if amount <= 0:
        raise CashDocumentValidationError(
            "CashDocument amount must be positive"
        )

    if not cash_desk.is_active:
        raise CashDocumentValidationError(
            "CashDesk is inactive"
        )

    cash_currency = _normalize_currency(
        cash_desk.currency_code
    )

    payment_currency = _normalize_currency(
        payment.currency_code
    )

    if cash_currency != currency:
        raise CashDocumentValidationError(
            "CashDocument currency does not match CashDesk"
        )

    if payment_currency != currency:
        raise CashDocumentValidationError(
            "CashDocument currency does not match Payment"
        )

    if payment.cash_desk_id != cash_desk.id:
        raise CashDocumentValidationError(
            "Payment cash source does not match CashDocument CashDesk"
        )

    if payment.bank_account_id is not None:
        raise CashDocumentValidationError(
            "Cash Payment cannot also have BankAccount source"
        )

    if payment.direction != facts.direction:
        raise CashDocumentValidationError(
            "CashDocument direction does not match Payment"
        )

    if Decimal(payment.amount) != amount:
        raise CashDocumentValidationError(
            "CashDocument amount does not match Payment"
        )

    if payment.payment_date != facts.document_date:
        raise CashDocumentValidationError(
            "CashDocument date does not match Payment"
        )

    if payment.counterparty_id != facts.counterparty_id:
        raise CashDocumentValidationError(
            "CashDocument counterparty does not match Payment"
        )

    if payment.contract_id != facts.contract_id:
        raise CashDocumentValidationError(
            "CashDocument contract does not match Payment"
        )

    return currency, amount


async def create_cash_document_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    payment_id: int,
    facts: CashDocumentFacts,
    created_by: int,
) -> CashDocument:
    """
    Persist immutable CashDocument evidence for an existing Payment.

    This function does not create/confirm/cancel Payment and does not
    create JournalEntry. Caller owns the transaction.
    """

    cash_desk = await _get_cash_desk(
        db,
        company_id=company_id,
        cash_desk_id=cash_desk_id,
    )

    payment = await _get_payment(
        db,
        company_id=company_id,
        payment_id=payment_id,
        for_update=True,
    )

    currency, amount = (
        _validate_document_payment_identity(
            cash_desk=cash_desk,
            payment=payment,
            facts=facts,
        )
    )

    document = CashDocument(
        company_id=company_id,
        cash_desk_id=cash_desk.id,
        payment_id=payment.id,
        document_number=_normalize_number(
            facts.document_number
        ),
        direction=facts.direction,
        document_date=facts.document_date,
        amount=amount,
        currency_code=currency,
        counterparty_id=facts.counterparty_id,
        contract_id=facts.contract_id,
        external_reference=_normalize_optional_text(
            facts.external_reference
        ),
        description=_normalize_optional_text(
            facts.description
        ),
        created_by=created_by,
        reversal_of_id=None,
    )

    db.add(document)
    await db.flush()

    return document


async def reverse_cash_document_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    cash_document_id: int,
    reversal_document_number: str,
    created_by: int,
) -> CashDocument:
    """
    Append an immutable reversal evidence row.

    The reversal preserves the original CashDesk and Payment identity.
    It does not reverse Payment or GL. Monetary reversal belongs to the
    Payment/orchestration layer.
    """

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

    existing_reversal = (
        await db.execute(
            select(CashDocument.id).where(
                CashDocument.company_id == company_id,
                CashDocument.reversal_of_id == original.id,
            )
        )
    ).scalar_one_or_none()

    if existing_reversal is not None:
        raise CashDocumentAlreadyReversedError(
            "CashDocument already has reversal evidence"
        )

    reversal = CashDocument(
        company_id=original.company_id,
        cash_desk_id=original.cash_desk_id,
        payment_id=original.payment_id,
        document_number=_normalize_number(
            reversal_document_number
        ),
        direction=original.direction,
        document_date=original.document_date,
        amount=original.amount,
        currency_code=original.currency_code,
        counterparty_id=original.counterparty_id,
        contract_id=original.contract_id,
        external_reference=original.external_reference,
        description=original.description,
        created_by=created_by,
        reversal_of_id=original.id,
    )

    db.add(reversal)
    await db.flush()

    return reversal


async def create_cash_document_reentry_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    reversed_cash_document_id: int,
    cash_desk_id: int,
    payment_id: int,
    facts: CashDocumentFacts,
    created_by: int,
) -> CashDocument:
    """
    Append replacement evidence after an original has been reversed.

    Re-entry is a new original row (reversal_of_id=NULL), never an
    UPDATE of historical CashDocument.
    """

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

    reversal_exists = (
        await db.execute(
            select(CashDocument.id).where(
                CashDocument.company_id == company_id,
                CashDocument.reversal_of_id == original.id,
            )
        )
    ).scalar_one_or_none()

    if reversal_exists is None:
        raise CashDocumentValidationError(
            "CashDocument must be reversed before re-entry"
        )

    return await create_cash_document_evidence(
        db,
        company_id=company_id,
        cash_desk_id=cash_desk_id,
        payment_id=payment_id,
        facts=facts,
        created_by=created_by,
    )
