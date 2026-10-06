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
