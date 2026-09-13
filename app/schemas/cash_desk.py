from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


def _normalize_required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value cannot be blank")
    return normalized


class CashDeskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=255,
    )
    code: str = Field(
        min_length=1,
        max_length=100,
    )
    currency_code: str = Field(
        default="UAH",
        min_length=3,
        max_length=3,
    )
    accounting_account_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_required_text(value)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return _normalize_required_text(value).upper()

    @field_validator("currency_code")
    @classmethod
    def normalize_currency_code(cls, value: str) -> str:
        return value.strip().upper()


class CashDeskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
    )
    accounting_account_id: int | None = Field(
        default=None,
        gt=0,
    )
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value)

    @field_validator("code")
    @classmethod
    def normalize_code(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value).upper()

    @field_validator("currency_code")
    @classmethod
    def normalize_currency_code(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return value.strip().upper()


class CashDeskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    name: str
    code: str
    currency_code: str
    accounting_account_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
