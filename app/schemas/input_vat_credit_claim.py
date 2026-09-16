from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistrationSuspension(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    suspended_on: date
    resumed_on: date
    decision_reference: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def chronological(self):
        if self.resumed_on <= self.suspended_on:
            raise ValueError('resumed_on must follow suspended_on; interval is [suspended_on, resumed_on)')
        return self


class InputVatCreditClaimCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=100)
    tax_calculation_id: int = Field(gt=0)
    invoice_number: str = Field(min_length=1, max_length=120)
    invoice_date: date
    registration_status: Literal['pending', 'suspended', 'rejected', 'registered']
    registered_on: date | None = None
    receipt_reference: str | None = Field(default=None, min_length=1, max_length=500)
    buyer_vat_number: str = Field(min_length=1, max_length=20)
    supplier_vat_number: str = Field(min_length=1, max_length=20)
    claim_period: date
    taxable_base: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    tax_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    suspensions: list[RegistrationSuspension] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def period_and_registration(self):
        if self.claim_period.day != 1:
            raise ValueError('claim_period must be the first day of the selected month')
        if self.registration_status == 'registered' and (self.registered_on is None or not self.receipt_reference):
            raise ValueError('Registered PN requires registered_on and receipt_reference')
        return self


class InputVatCreditEligibility(BaseModel):
    eligible: bool
    reason: str
    policy_version: str
    registration_deadline: date | None = None
    timely_registration: bool | None = None
    first_eligible_period: date | None = None
    expires_on: date | None = None
    credit_available_date: date | None = None
