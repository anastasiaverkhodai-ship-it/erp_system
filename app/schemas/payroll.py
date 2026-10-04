from datetime import date, datetime
from calendar import monthrange
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.payroll import (
    PayrollInputSource,
    PayrollPeriodStatus,
)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class PayrollPeriodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int = Field(gt=0)
    month: int = Field(ge=1, le=12)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self) -> "PayrollPeriodCreate":
        expected_start = date(self.year, self.month, 1)
        expected_end = date(self.year, self.month, monthrange(self.year, self.month)[1])
        if self.start_date != expected_start or self.end_date != expected_end:
            raise ValueError("Payroll period must cover its complete calendar month")
        return self


class PayrollPeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int

    year: int
    month: int
    start_date: date
    end_date: date

    status: PayrollPeriodStatus

    created_by: int

    finalized_by: int | None
    finalized_at: datetime | None

    created_at: datetime
    updated_at: datetime


class PayrollInputCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employment_contract_id: int = Field(gt=0)

    manual_adjustment_amount: Decimal = Field(
        default=Decimal("0.00"),
        max_digits=18,
        decimal_places=2,
    )

    manual_adjustment_reason: str | None = Field(
        default=None,
        max_length=4000,
    )

    @field_validator("manual_adjustment_reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_adjustment(self) -> "PayrollInputCreate":
        if (
            self.manual_adjustment_amount != Decimal("0")
            and self.manual_adjustment_reason is None
        ):
            raise ValueError(
                "manual_adjustment_reason is required "
                "when manual_adjustment_amount is non-zero"
            )
        return self


class PayrollInputUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_adjustment_amount: Decimal | None = Field(
        default=None,
        max_digits=18,
        decimal_places=2,
    )

    manual_adjustment_reason: str | None = Field(
        default=None,
        max_length=4000,
    )

    @field_validator("manual_adjustment_reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        return _optional_text(value)


class PayrollInputSalarySliceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_input_id: int
    salary_rate_id: int

    salary_rate_type: str
    salary_rate_amount: Decimal
    currency_code: str

    effective_from: date
    effective_to: date

    created_at: datetime


class PayrollInputRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_period_id: int
    employment_contract_id: int

    monthly_norm_minutes: int | None = None
    scheduled_minutes: int
    worked_minutes: int

    leave_days: int
    sick_days: int

    manual_adjustment_amount: Decimal
    manual_adjustment_reason: str | None

    source: PayrollInputSource

    created_by: int
    created_at: datetime
    updated_at: datetime


class PayrollInputDetailRead(PayrollInputRead):
    salary_slices: list[PayrollInputSalarySliceRead] = Field(
        default_factory=list
    )
