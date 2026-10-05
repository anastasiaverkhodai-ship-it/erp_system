from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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
    calculated_by: int
    calculated_at: datetime


class PayrollCalculationDetailRead(PayrollCalculationRead):
    lines: list[PayrollCalculationLineRead] = []
