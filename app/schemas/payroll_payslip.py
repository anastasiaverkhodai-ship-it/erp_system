from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PayrollPayslipLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_payslip_id: int
    line_no: int
    line_kind: str
    component_code: str
    description: str | None
    amount: Decimal
    currency_code: str
    source_payroll_calculation_line_id: int | None
    source_payroll_statutory_result_line_id: int | None
    source_payroll_deduction_result_line_id: int | None
    created_at: datetime


class PayrollPayslipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_calculation_id: int
    payroll_statutory_result_id: int
    payroll_period_id: int
    employment_contract_id: int
    employee_id: int

    employee_number: str
    employee_first_name: str
    employee_last_name: str
    employee_middle_name: str | None
    employee_tax_number: str | None
    contract_number: str

    currency_code: str
    gross_amount: Decimal
    employee_withholding_amount: Decimal
    employer_contribution_amount: Decimal
    net_amount: Decimal
    payroll_deduction_result_id: int | None
    non_statutory_deduction_amount: Decimal
    final_payable_amount: Decimal

    generated_by: int
    generated_at: datetime


class PayrollPayslipDetailRead(PayrollPayslipRead):
    lines: list[PayrollPayslipLineRead]
