from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


class CashDocumentPaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_desk_id: int = Field(gt=0)

    payment_number: str = Field(
        min_length=1,
        max_length=100,
    )
    document_number: str = Field(
        min_length=1,
        max_length=100,
    )

    direction: PaymentDirection
    document_date: date

    amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
    )

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

    @field_validator(
        "payment_number",
        "document_number",
    )
    @classmethod
    def normalize_number(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "number cannot be blank"
            )

        return value

    @field_validator("currency_code")
    @classmethod
    def normalize_currency(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()


class CashDocumentPaymentReverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reversal_document_number: str = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("reversal_document_number")
    @classmethod
    def normalize_number(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "reversal_document_number cannot be blank"
            )

        return value


class CashDocumentPaymentResponse(BaseModel):
    payment_id: int
    payment_status: PaymentStatus

    cash_document_id: int
    cash_desk_id: int

    document_number: str
    direction: PaymentDirection
    document_date: date
    amount: Decimal
    currency_code: str

    reversal_of_id: int | None
