from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.services.payment_types import PaymentDirection


class CashDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    cash_desk_id: int
    payment_id: int
    document_number: str
    direction: PaymentDirection
    document_date: date
    amount: Decimal
    currency_code: str
    counterparty_id: int
    contract_id: int | None
    external_reference: str | None
    description: str | None
    created_by: int
    created_at: datetime
    reversal_of_id: int | None


class CashDocumentCreateCommand(BaseModel):
    """
    Command facts supplied by caller.

    Monetary/source facts are validated against CashDesk
    and later routed through Payment lifecycle.
    """

    model_config = ConfigDict(extra="forbid")

    cash_desk_id: int = Field(gt=0)
    document_number: str = Field(
        min_length=1,
        max_length=100,
    )
    direction: PaymentDirection
    document_date: date
    amount: Decimal = Field(gt=0)
    currency_code: str = Field(
        min_length=3,
        max_length=3,
    )
    counterparty_id: int = Field(gt=0)
    contract_id: int | None = Field(
        default=None,
        gt=0,
    )
    external_reference: str | None = Field(
        default=None,
        max_length=255,
    )
    description: str | None = Field(
        default=None,
        max_length=500,
    )

    @field_validator("document_number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(
                "document_number cannot be blank"
            )
        return value

    @field_validator("currency_code")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()
