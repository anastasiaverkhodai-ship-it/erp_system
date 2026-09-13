from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.payment_types import PaymentDirection


class CashDocumentError(ValueError):
    pass


class CashDocumentNotFoundError(CashDocumentError):
    pass


class CashDocumentValidationError(CashDocumentError):
    pass


class CashDocumentAlreadyReversedError(
    CashDocumentError
):
    pass


@dataclass(frozen=True)
class CashDocumentFacts:
    document_number: str
    direction: PaymentDirection
    document_date: date
    amount: Decimal
    currency_code: str
    counterparty_id: int
    contract_id: int | None = None
    external_reference: str | None = None
    description: str | None = None
