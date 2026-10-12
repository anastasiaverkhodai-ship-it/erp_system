from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FundingLineInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    payroll_calculation_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0,max_digits=18,decimal_places=2)

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value):
        return value.quantize(Decimal("0.01"))


class InsuranceFundingCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    journal_entry_id: int = Field(gt=0)
    bank_account_id: int = Field(gt=0)
    document_reference: str = Field(min_length=1,max_length=500)
    separate_account_reference: str = Field(min_length=1,max_length=500)
    request_key: str = Field(min_length=1,max_length=200)
    allocations: list[FundingLineInput] = Field(min_length=1,max_length=500)

    @field_validator('document_reference','separate_account_reference','request_key')
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


class InsuranceFundingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    journal_entry_id: int
    bank_account_id: int
    amount: Decimal
    document_reference: str
    separate_account_reference: str
    created_by: int
    created_at: datetime
    receipt_date: date
    status: str
    allocations: list[FundingLineInput]


class InsuranceFundingReverse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reversal_date: date
