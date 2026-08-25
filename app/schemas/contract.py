from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.contract_types import (
    ContractStatus,
    ContractType,
)
from app.services.currency_catalog_service import (
    CurrencyNotFoundError,
    SYSTEM_CURRENCY_CATALOG,
)


def _normalize_currency_code(
    value,
):
    if not isinstance(value, str):
        return value

    code = value.strip().upper()

    try:
        SYSTEM_CURRENCY_CATALOG.get(
            code
        )
    except CurrencyNotFoundError as exc:
        raise ValueError(
            (
                "Unsupported currency code: "
                f"{code}"
            )
        ) from exc

    return code


class ContractCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    counterparty_id: int = Field(
        gt=0,
    )

    number: str = Field(
        min_length=1,
        max_length=100,
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    contract_type: ContractType

    status: ContractStatus = (
        ContractStatus.DRAFT
    )

    start_date: date

    end_date: date | None = None

    currency_code: str = Field(
        default="UAH",
        min_length=3,
        max_length=3,
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
        "name",
        mode="before",
    )
    @classmethod
    def empty_name_to_none(
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
        "currency_code",
        mode="before",
    )
    @classmethod
    def validate_currency_code(
        cls,
        value,
    ):
        return _normalize_currency_code(
            value
        )

    @model_validator(
        mode="after",
    )
    def validate_date_range(self):
        if (
            self.end_date is not None
            and self.end_date
            < self.start_date
        ):
            raise ValueError(
                (
                    "end_date cannot be "
                    "earlier than start_date"
                )
            )

        return self


class ContractUpdate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    counterparty_id: int | None = Field(
        default=None,
        gt=0,
    )

    number: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    contract_type: (
        ContractType | None
    ) = None

    status: (
        ContractStatus | None
    ) = None

    start_date: date | None = None

    end_date: date | None = None

    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
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

    @field_validator(
        "name",
        mode="before",
    )
    @classmethod
    def empty_name_to_none(
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
        "currency_code",
        mode="before",
    )
    @classmethod
    def validate_currency_code(
        cls,
        value,
    ):
        if value is None:
            return None

        return _normalize_currency_code(
            value
        )


class ContractResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    counterparty_id: int

    number: str
    name: str | None

    contract_type: ContractType
    status: ContractStatus

    start_date: date
    end_date: date | None

    currency_code: str

    payment_term_days: int
    credit_limit: Decimal

    created_at: datetime
    updated_at: datetime
