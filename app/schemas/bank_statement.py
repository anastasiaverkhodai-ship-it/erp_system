from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _required_text(
    value: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(
            "value cannot be blank"
        )

    return normalized


class BankStatementCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    bank_account_id: int = Field(gt=0)

    external_id: str = Field(
        min_length=1,
        max_length=255,
    )

    statement_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None

    source_type: str = Field(
        default="manual",
        min_length=1,
        max_length=50,
    )

    source_reference: str | None = Field(
        default=None,
        max_length=500,
    )

    @field_validator(
        "external_id",
        "source_type",
    )
    @classmethod
    def normalize_required_text(
        cls,
        value: str,
    ) -> str:
        return _required_text(value)

    @model_validator(mode="after")
    def validate_period(self):
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end
            < self.period_start
        ):
            raise ValueError(
                "period_end cannot be before "
                "period_start"
            )

        return self


class BankStatementLineCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    external_line_id: str = Field(
        min_length=1,
        max_length=255,
    )

    transaction_date: date
    value_date: date | None = None

    amount: Decimal

    currency_code: str = Field(
        min_length=3,
        max_length=3,
    )

    counterparty_name: str | None = Field(
        default=None,
        max_length=500,
    )

    counterparty_account: str | None = Field(
        default=None,
        max_length=255,
    )

    payment_reference: str | None = Field(
        default=None,
        max_length=1000,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )

    raw_payload: str | None = None

    @field_validator("external_line_id")
    @classmethod
    def normalize_external_line_id(
        cls,
        value: str,
    ) -> str:
        return _required_text(value)

    @field_validator("currency_code")
    @classmethod
    def normalize_currency_code(
        cls,
        value: str,
    ) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_amount(self):
        if self.amount == 0:
            raise ValueError(
                "amount cannot be zero"
            )

        return self


class BankStatementResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    bank_account_id: int
    external_id: str
    statement_date: date | None
    period_start: date | None
    period_end: date | None
    source_type: str
    source_reference: str | None
    created_by: int | None
    created_at: datetime


class BankStatementLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    bank_statement_id: int
    bank_account_id: int
    external_line_id: str
    transaction_date: date
    value_date: date | None
    amount: Decimal
    currency_code: str
    counterparty_name: str | None
    counterparty_account: str | None
    payment_reference: str | None
    description: str | None
    raw_payload: str | None
    created_at: datetime
