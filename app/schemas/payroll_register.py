from decimal import Decimal
from pydantic import BaseModel, Field
from datetime import date


class PayrollRegisterRow(BaseModel):
    payroll_input_id: int
    employment_contract_id: int
    employee_id: int
    payroll_calculation_id: int | None
    currency_code: str | None
    gross_amount: Decimal | None
    employee_withholding_amount: Decimal | None
    employer_contribution_amount: Decimal | None
    net_amount: Decimal | None
    accrual_status: str
    paid_amount: Decimal
    reserved_amount: Decimal
    advance_paid_amount: Decimal = Decimal(0)
    advance_reserved_amount: Decimal = Decimal(0)
    deduction_amount: Decimal | None = None
    deduction_posted_amount: Decimal = Decimal(0)
    final_payable_amount: Decimal | None = None
    recognized_balance: Decimal | None
    available_to_pay: Decimal | None
    issues: list[str]


class PayrollRegisterAdvance(BaseModel):
    id: int
    employment_contract_id: int
    payment_date: date
    currency_code: str
    amount: Decimal
    status: str


class PayrollRegister(BaseModel):
    company_id: int
    payroll_period_id: int
    period_status: str
    scope: str = 'Current settlements for calculations in this period; includes advances and deductions; excludes opening debts'
    advances: list[PayrollRegisterAdvance] = Field(default_factory=list)
    complete: bool
    rows: list[PayrollRegisterRow]
