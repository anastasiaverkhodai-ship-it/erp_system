from typing import Literal
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaxRemittanceLineInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    payroll_calculation_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0,max_digits=18,decimal_places=2)

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value):
        return value.quantize(Decimal("0.01"))


class TaxRemittanceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    component: Literal['personal_income_tax','military_levy','unified_social_contribution']
    journal_entry_id: int = Field(gt=0)
    bank_account_id: int = Field(gt=0)
    document_reference: str = Field(min_length=1,max_length=500)
    budget_payment_reference: str = Field(min_length=1,max_length=500)
    request_key: str = Field(min_length=1,max_length=200)
    allocations: list[TaxRemittanceLineInput] = Field(min_length=1,max_length=500)

    @field_validator('document_reference','budget_payment_reference','request_key')
    @classmethod
    def nonblank(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('Reference cannot be blank')
        return value

    @model_validator(mode='after')
    def distinct_sources(self):
        if len({line.payroll_calculation_id for line in self.allocations}) != len(self.allocations):
            raise ValueError('Each payroll calculation can occur once')
        return self


class TaxRemittanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    component: str
    journal_entry_id: int
    bank_account_id: int
    amount: Decimal
    document_reference: str
    budget_payment_reference: str
    created_by: int
    created_at: datetime
    payment_date: date
    status: str
    allocations: list[TaxRemittanceLineInput]


class TaxRemittanceReverse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reversal_date: date


class TaxBalanceTotals(BaseModel):
    component: str
    accrued_amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal


class TaxBalanceRow(TaxBalanceTotals):
    payroll_calculation_id: int
    employee_id: int
    employment_contract_id: int
    period_start: date
    period_end: date
    accrual_journal_id: int


class TaxBalanceRead(BaseModel):
    company_id: int
    as_of: date
    currency_code: str
    scope: str
    rows: list[TaxBalanceRow]
    totals: list[TaxBalanceTotals]
