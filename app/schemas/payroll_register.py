from decimal import Decimal
from pydantic import BaseModel


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
    recognized_balance: Decimal | None
    available_to_pay: Decimal | None
    issues: list[str]


class PayrollRegister(BaseModel):
    company_id: int
    payroll_period_id: int
    period_status: str
    scope: str = 'Current settlements for calculations in this period; excludes opening debts and advances'
    complete: bool
    rows: list[PayrollRegisterRow]
