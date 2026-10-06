from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PayrollDeductionInstructionCreate(BaseModel):
    employment_contract_id: int
    deduction_type: str = Field(min_length=1, max_length=50)
    method: str
    fixed_amount: Decimal | None = None
    percentage: Decimal | None = None
    currency_code: str = Field(default="UAH", min_length=3, max_length=3)
    priority: int = Field(default=100, ge=0)
    effective_from: date
    effective_to: date | None = None
    request_key: str = Field(min_length=1, max_length=100)
    source_reference: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_instruction(self):
        if self.method not in {"fixed", "percentage"}:
            raise ValueError("method must be fixed or percentage")

        if self.effective_to is not None:
            if self.effective_to < self.effective_from:
                raise ValueError(
                    "effective_to cannot precede effective_from"
                )

        if self.method == "fixed":
            if self.fixed_amount is None or self.fixed_amount <= 0:
                raise ValueError(
                    "fixed deduction requires positive fixed_amount"
                )
            if self.percentage is not None:
                raise ValueError(
                    "fixed deduction cannot define percentage"
                )

        if self.method == "percentage":
            if (
                self.percentage is None
                or self.percentage <= 0
                or self.percentage > 100
            ):
                raise ValueError(
                    "percentage deduction requires percentage in (0, 100]"
                )
            if self.fixed_amount is not None:
                raise ValueError(
                    "percentage deduction cannot define fixed_amount"
                )

        return self


class PayrollDeductionInstructionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employment_contract_id: int
    deduction_type: str
    method: str
    fixed_amount: Decimal | None
    percentage: Decimal | None
    currency_code: str
    priority: int
    effective_from: date
    effective_to: date | None
    request_key: str
    source_reference: str | None
    created_by: int
    created_at: datetime

class PayrollDeductionResultLineRead(BaseModel):
    id: int
    company_id: int
    payroll_deduction_result_id: int
    payroll_deduction_instruction_id: int
    line_no: int
    deduction_type: str
    method: str
    calculation_base_amount: Decimal
    fixed_amount: Decimal | None = None
    percentage: Decimal | None = None
    amount: Decimal
    priority: int
    currency_code: str
    source_reference: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PayrollDeductionResultRead(BaseModel):
    id: int
    company_id: int
    payroll_calculation_id: int
    payroll_statutory_result_id: int
    employment_contract_id: int
    statutory_net_amount: Decimal
    deduction_amount: Decimal
    final_payable_amount: Decimal
    currency_code: str
    calculated_by: int
    calculated_at: datetime
    lines: list[PayrollDeductionResultLineRead] = []

    model_config = ConfigDict(from_attributes=True)
