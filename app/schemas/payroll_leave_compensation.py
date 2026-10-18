from pydantic import BaseModel, ConfigDict, Field, field_validator
from decimal import Decimal


class CompensationSalarySubstitution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    employment_fraction: Decimal = Field(gt=0, le=1, max_digits=7, decimal_places=6)
    source_reference: str = Field(min_length=1, max_length=1000)

    @field_validator('source_reference')
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Employment fraction requires documentary evidence')
        return value


class LeaveCompensationCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    salary_substitution: CompensationSalarySubstitution | None = None
    termination_event_id: int=Field(gt=0)
    payroll_input_id: int=Field(gt=0)
    request_key: str=Field(min_length=1,max_length=100)
    entitlement_review_reference: str=Field(min_length=1,max_length=1000)

    @field_validator('request_key','entitlement_review_reference')
    @classmethod
    def nonempty(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('Document reference cannot be blank')
        return value


from datetime import date
from decimal import Decimal


class LeaveCompensationRead(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    employment_contract_id: int
    termination_event_id: int
    payroll_input_id: int
    compensation_date: date
    gross_amount: Decimal
    source_reference: str


class LeaveCompensationLineRead(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    compensation_id: int
    account_id: int
    balance_entry_id: int
    acquisition_bucket: str
    consumed_days: Decimal
    compensated_days: Decimal
    amount: Decimal
    basis_snapshot: dict


class LeaveCompensationReverse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    effective_date: date
    source_reference: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=1, max_length=100)

    @field_validator('source_reference', 'request_key')
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Reversal reference cannot be blank')
        return value


class LeaveCompensationReversalRead(LeaveCompensationReverse):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    compensation_id: int
