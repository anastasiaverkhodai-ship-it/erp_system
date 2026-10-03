from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PayrollDisbursementCreate(BaseModel):
    bank_account_id: int
    payment_date: date


class PayrollDisbursementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_calculation_id: int
    employee_id: int
    bank_account_id: int
    employee_iban_snapshot: str
    amount: Decimal
    currency_code: str
    payment_date: date
    created_by: int
    created_at: datetime
