from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.payroll_statutory import PayrollStatutoryComponent


class PayrollStatutoryRateCreate(BaseModel):
    component: PayrollStatutoryComponent
    rate: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    effective_from: date
    effective_to: date | None = None


class PayrollStatutoryRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    component: PayrollStatutoryComponent
    rate: Decimal
    effective_from: date
    effective_to: date | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class PayrollStatutoryResultLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_statutory_result_id: int
    line_no: int
    component: PayrollStatutoryComponent
    base_amount: Decimal
    rate: Decimal
    amount: Decimal
    currency_code: str
    source_rate_id: int
    rate_effective_from: date
    rate_effective_to: date | None
    created_at: datetime


class PayrollStatutoryResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_calculation_id: int
    currency_code: str
    gross_amount: Decimal
    employee_withholding_amount: Decimal
    employer_contribution_amount: Decimal
    net_amount: Decimal
    calculated_by: int
    calculated_at: datetime


class PayrollStatutoryResultDetailRead(
    PayrollStatutoryResultRead
):
    lines: list[PayrollStatutoryResultLineRead] = Field(
        default_factory=list
    )
