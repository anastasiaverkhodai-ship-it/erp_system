from datetime import date, datetime
from decimal import Decimal
from typing import Literal

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
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    full_time_norm_minutes: int | None = Field(default=None, gt=0, strict=True)
    norm_reference: str | None = Field(default=None, min_length=1, max_length=500)
    manual_adjustment_category: Literal[
        'ordinary_salary', 'production_bonus', 'regular_allowance', 'indexation',
        'night', 'overtime', 'hazardous', 'travelling', 'holiday_bonus',
        'jubilee_bonus', 'hardship', 'reimbursement',
    ] | None = None
    manual_adjustment_reference: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode='after')
    def paired_evidence(self):
        if (self.full_time_norm_minutes is None) != (self.norm_reference is None):
            raise ValueError('Full-time norm and documentary reference must be supplied together')
        if (self.manual_adjustment_category is None) != (self.manual_adjustment_reference is None):
            raise ValueError('Adjustment category and documentary reference must be supplied together')
        return self
