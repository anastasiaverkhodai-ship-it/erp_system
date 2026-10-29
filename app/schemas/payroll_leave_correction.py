from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.payroll_leave_compensation import CompensationSalarySubstitution


class LeaveCorrectionRange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start: date
    end: date

    @model_validator(mode='after')
    def ordered(self):
        if self.end<self.start:raise ValueError('Invalid leave date range')
        return self


class LeaveCorrectionRequestCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    target_kind: Literal['vacation','compensation']
    target_id: int=Field(gt=0)
    correction_date: date
    request_key: str=Field(min_length=1,max_length=90)
    reason: str=Field(min_length=10,max_length=2000)
    order_reference: str=Field(min_length=3,max_length=1000)
    consent_reference: str | None=Field(default=None,min_length=3,max_length=1000)
    operation: Literal['partial','recall','transfer'] | None=None
    retained_ranges: list[LeaveCorrectionRange]=Field(default_factory=list)
    transferred_ranges: list[LeaveCorrectionRange]=Field(default_factory=list)
    effective_date: date | None=None
    annual_continuity_reference: str | None=Field(default=None,min_length=10,max_length=1000)
    recall_ground: str | None=None
    legal_reference: str | None=None
    salary_substitution: CompensationSalarySubstitution | None=None

    @model_validator(mode='after')
    def target_fields(self):
        if self.target_kind=='vacation':
            if self.operation is None or self.effective_date is None or not self.consent_reference:
                raise ValueError('Vacation change requires operation, effective date and consent')
            if self.correction_date<self.effective_date:
                raise ValueError('Financial correction cannot precede leave change')
        elif self.operation or self.retained_ranges or self.transferred_ranges:
            raise ValueError('Compensation correction does not change leave dates')
        return self
