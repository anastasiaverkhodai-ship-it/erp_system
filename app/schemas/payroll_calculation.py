from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PayrollCalculationLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_calculation_id: int
    line_no: int
    line_type: str
    description: str | None
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    currency_code: str
    salary_rate_type: str | None
    source_supplement_id: int | None = None
    source_salary_rate_id: int | None
    source_effective_from: date | None
    source_effective_to: date | None
    created_at: datetime


class PayrollCalculationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_period_id: int
    payroll_input_id: int
    employment_contract_id: int
    status: str
    currency_code: str
    gross_amount: Decimal
    minimum_wage_assessment: dict | None = None
    calculated_by: int
    calculated_at: datetime


class PayrollCalculationDetailRead(PayrollCalculationRead):
    lines: list[PayrollCalculationLineRead] = []


class PayrollMinimumWageContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_time_norm_minutes: int = Field(gt=0)
    norm_reference: str = Field(min_length=1,max_length=500)

    @model_validator(mode='after')
    def normalize_reference(self):
        self.norm_reference = self.norm_reference.strip()
        if not self.norm_reference:
            raise ValueError('Norm reference cannot be blank')
        return self
