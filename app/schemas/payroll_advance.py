from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PayrollAdvanceCreate(BaseModel):
    payroll_period_id: int
    employment_contract_id: int
    bank_account_id: int

    advance_percentage: Decimal = Field(gt=0, le=100)
    calculation_base_amount: Decimal = Field(gt=0)
    minimum_due_amount: Decimal = Field(ge=0)

    currency_code: str = Field(min_length=3, max_length=3)
    payment_date: date


class PayrollAdvanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_period_id: int
    employment_contract_id: int
    bank_account_id: int

    advance_percentage: Decimal
    calculation_base_amount: Decimal
    calculated_amount: Decimal
    minimum_due_amount: Decimal
    paid_amount: Decimal

    currency_code: str
    payment_date: date

    created_by: int
    created_at: datetime


class PayrollAdvanceBasisSource(BaseModel):
    salary_rate_id: int
    effective_from: date
    effective_to: date
    rate_type: str
    rate_amount: Decimal
    planned_minutes: int
    worked_minutes: int
    attendance_ids: list[int]
    base_amount: Decimal
    minimum_amount: Decimal


class PayrollAdvanceBasisRead(BaseModel):
    company_id: int
    payroll_period_id: int
    employment_contract_id: int
    earned_through: date
    currency_code: str
    advance_percentage: Decimal
    monthly_norm_minutes: int
    calculation_base_gross: Decimal
    percentage_gross: Decimal
    minimum_gross: Decimal
    recommended_gross: Decimal
    percentage_meets_minimum: bool
    sources: list[PayrollAdvanceBasisSource]


class PayrollAdvanceBankMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    payroll_advance_id: int
    bank_statement_line_id: int
    matched_amount: Decimal
    currency_code: str
    reversal_of_id: int | None
