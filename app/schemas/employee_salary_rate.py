from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.employee_salary_rate import SalaryRateType


class SalaryRateCreate(BaseModel):
    employment_contract_id: int
    rate_type: SalaryRateType
    amount: Decimal = Field(gt=0)
    currency_code: str
    effective_from: date
    effective_to: date | None = None

    @field_validator("currency_code")
    @classmethod
    def normalize_currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError(
                "currency_code must contain exactly 3 letters"
            )
        return value


class SalaryRateUpdate(BaseModel):
    rate_type: SalaryRateType | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency_code: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator("currency_code")
    @classmethod
    def normalize_currency_code(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip().upper()

        if len(value) != 3 or not value.isalpha():
            raise ValueError(
                "currency_code must contain exactly 3 letters"
            )

        return value


class SalaryRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employment_contract_id: int
    rate_type: SalaryRateType
    amount: Decimal
    currency_code: str
    effective_from: date
    effective_to: date | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime
