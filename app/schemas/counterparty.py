from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.services.counterparty_types import (
    CounterpartyType,
    CounterpartyVatStatus,
)


class CounterpartyCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    short_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    counterparty_type: CounterpartyType = (
        CounterpartyType.BOTH
    )

    edrpou: str | None = Field(
        default=None,
        min_length=8,
        max_length=8,
        pattern=r"^\d{8}$",
    )

    tax_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+$",
    )

    vat_status: CounterpartyVatStatus = (
        CounterpartyVatStatus.UNKNOWN
    )

    vat_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+$",
    )

    default_currency_code: str = Field(
        default="UAH",
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    payment_term_days: int = Field(
        default=0,
        ge=0,
    )

    credit_limit: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        max_digits=18,
        decimal_places=2,
    )

    @field_validator(
        "short_name",
        "edrpou",
        "tax_number",
        "vat_number",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(
        cls,
        value,
    ):
        if (
            isinstance(value, str)
            and not value.strip()
        ):
            return None

        return value

    @field_validator(
        "default_currency_code",
        mode="before",
    )
    @classmethod
    def normalize_currency_code(
        cls,
        value,
    ):
        if isinstance(value, str):
            return value.strip().upper()

        return value


class CounterpartyUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    short_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    counterparty_type: (
        CounterpartyType | None
    ) = None

    edrpou: str | None = Field(
        default=None,
        min_length=8,
        max_length=8,
        pattern=r"^\d{8}$",
    )

    tax_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+$",
    )

    vat_status: (
        CounterpartyVatStatus | None
    ) = None

    vat_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+$",
    )

    default_currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )

    payment_term_days: int | None = Field(
        default=None,
        ge=0,
    )

    credit_limit: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        max_digits=18,
        decimal_places=2,
    )

    is_active: bool | None = None

    @field_validator(
        "short_name",
        "edrpou",
        "tax_number",
        "vat_number",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(
        cls,
        value,
    ):
        if (
            isinstance(value, str)
            and not value.strip()
        ):
            return None

        return value

    @field_validator(
        "default_currency_code",
        mode="before",
    )
    @classmethod
    def normalize_currency_code(
        cls,
        value,
    ):
        if isinstance(value, str):
            return value.strip().upper()

        return value


class CounterpartyResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int

    name: str
    short_name: str | None

    counterparty_type: CounterpartyType

    edrpou: str | None
    tax_number: str | None

    vat_status: CounterpartyVatStatus
    vat_number: str | None

    default_currency_code: str

    payment_term_days: int
    credit_limit: Decimal

    is_active: bool

    created_at: datetime
    updated_at: datetime
