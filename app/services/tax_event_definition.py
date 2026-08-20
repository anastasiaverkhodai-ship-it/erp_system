from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.tax_event_types import (
    TaxEventType,
)


@dataclass(frozen=True, slots=True)
class TaxEventDefinition:
    """
    Universal tax-relevant business event.

    amount
        Monetary amount represented by the event.

        Its precise tax meaning depends on the event type.
        For example, a payment event contains a payment
        amount and must not automatically be interpreted
        as a taxable base.
    """

    company_id: int
    source_document_id: int
    event_date: date
    event_type: TaxEventType
    amount: Decimal
    currency_code: str
    source_document_line_id: int | None = None

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if self.source_document_id <= 0:
            raise ValueError(
                "Source document ID must be greater than zero"
            )

        if (
            self.source_document_line_id is not None
            and self.source_document_line_id <= 0
        ):
            raise ValueError(
                "Source document line ID "
                "must be greater than zero"
            )

        if self.amount < 0:
            raise ValueError(
                "Tax event amount cannot be negative"
            )

        if (
            len(self.currency_code) != 3
            or not self.currency_code.isalpha()
            or self.currency_code
            != self.currency_code.upper()
        ):
            raise ValueError(
                "Currency code must contain exactly "
                "3 uppercase letters"
            )