from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


class PaymentCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    counterparty_id: int = Field(
        gt=0,
    )

    contract_id: int | None = Field(
        default=None,
        gt=0,
    )

    number: str = Field(
        min_length=1,
        max_length=100,
    )

    direction: PaymentDirection

    payment_date: date

    currency_code: str = Field(
        min_length=3,
        max_length=3,
    )

    amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
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
        "currency_code"
    )
    @classmethod
    def normalize_currency_code(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()


class PaymentResponse(BaseModel):
    id: int
    company_id: int
    counterparty_id: int
    contract_id: int | None

    number: str
    direction: PaymentDirection
    status: PaymentStatus

    payment_date: date
    currency_code: str
    amount: Decimal

    settled_amount: Decimal
    unallocated_amount: Decimal

    external_reference: str | None
    description: str | None

    created_by: int
    created_at: datetime
    updated_at: datetime

    confirmed_at: datetime | None
    cancelled_by: int | None
    cancelled_at: datetime | None


class PaymentSettlementAllocationCreateRequest(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid",
    )

    open_item_id: int = Field(
        gt=0,
    )

    amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
    )


class PaymentSettlementAllocationResponse(
    BaseModel
):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    payment_id: int
    open_item_id: int
    amount: Decimal

    status: (
        PaymentSettlementAllocationStatus
    )

    created_by: int
    created_at: datetime

    reversed_by: int | None
    reversed_at: datetime | None


class PaymentSettlementReconciliationResponse(
    BaseModel
):
    company_id: int
    payment_id: int

    direction: PaymentDirection
    status: PaymentStatus

    counterparty_id: int
    contract_id: int | None

    currency_code: str

    payment_amount: Decimal
    settled_amount: Decimal
    unallocated_amount: Decimal

    fully_allocated: bool

    allocations: list[
        PaymentSettlementAllocationResponse
    ]
