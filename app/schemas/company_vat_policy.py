from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompanyVatPolicyCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', revalidate_instances='always')
    effective_from: date
    payer_status: Literal['vat_payer', 'non_vat_payer']
    vat_number: str | None = Field(default=None, min_length=1, max_length=20)
    legal_basis: str = Field(min_length=1, max_length=500)
    allow_cash_method: bool = False

    @field_validator('vat_number', 'legal_basis', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_status(self):
        if self.payer_status == 'vat_payer' and not self.vat_number:
            raise ValueError('VAT payer requires its registration number')
        if self.payer_status == 'non_vat_payer' and (self.vat_number or self.allow_cash_method):
            raise ValueError('Non-payer cannot have VAT registration or cash method')
        return self


class CompanyVatPolicyResponse(CompanyVatPolicyCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    created_by: int
    created_at: datetime


class CounterpartyVatRegistrationCreate(CompanyVatPolicyCreate):
    allow_cash_method: Literal[False] = Field(default=False, exclude=True)


class CounterpartyVatRegistrationResponse(CounterpartyVatRegistrationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    counterparty_id: int
    created_by: int
    created_at: datetime
