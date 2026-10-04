from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PayrollDisbursementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_account_id: int = Field(gt=0)
    payment_date: date
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    request_key: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_partial_key(self):
        if self.request_key is not None:
            self.request_key = self.request_key.strip()
            if not self.request_key:
                raise ValueError('request_key cannot be blank')
        if self.amount is not None and self.request_key is None:
            raise ValueError('Partial disbursement requires request_key')
        return self


class PayrollDisbursementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    request_key: str
    payroll_calculation_id: int
    employee_id: int
    bank_account_id: int
    employee_iban_snapshot: str
    amount: Decimal
    currency_code: str
    payment_date: date
    confirmed_at: datetime | None
    reversed_on: date | None
    cancelled_at: datetime | None
    cancelled_by: int | None
    created_by: int
    created_at: datetime


class PayrollBankMatchCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bank_statement_line_id: int = Field(gt=0)
    matched_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency_code: str = Field(min_length=3, max_length=3)


class PayrollBankMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    payroll_disbursement_id: int
    bank_statement_line_id: int
    matched_amount: Decimal
    currency_code: str
    reversal_of_id: int | None
