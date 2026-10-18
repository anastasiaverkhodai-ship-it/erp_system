from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LeaveAllowanceSegment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date
    end: date
    annual_days: Decimal = Field(gt=0,le=365,decimal_places=2)
    disability_group: Literal['I','II','III'] | None = None
    eligibility_reference: str | None = Field(default=None,min_length=1,max_length=1000)

    @model_validator(mode='after')
    def disability_evidence(self):
        if self.disability_group is not None:
            if not self.eligibility_reference or not self.eligibility_reference.strip():
                raise ValueError('Disability leave requires dated supporting evidence')
            minimum = 26 if self.disability_group == 'III' else 30
            if self.annual_days < minimum:
                raise ValueError('Annual allowance is below the disability guarantee')
        return self


class LeaveAccountCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    employment_contract_id: int = Field(gt=0)
    leave_kind: Literal['annual','additional','social']
    entitlement_code: str = Field(min_length=1,max_length=80)
    period_start: date
    period_end: date
    segments: list[LeaveAllowanceSegment] = Field(default_factory=list,max_length=366)
    calendar_exclusions: list[date] = Field(default_factory=list,max_length=366)
    planned_work_dates: list[date] = Field(default_factory=list,max_length=366)
    excluded_service_dates: list[date] = Field(default_factory=list,max_length=366)
    social_days: Literal[10,17] | None = None
    social_grounds: list[str] = Field(default_factory=list,max_length=10)
    opening_id: int | None = Field(default=None,gt=0)
    opening_before_2024: Decimal | None = Field(default=None,ge=0,decimal_places=2)
    source_reference: str = Field(min_length=1,max_length=1000)
    request_key: str = Field(min_length=1,max_length=100)

    @field_validator('entitlement_code','source_reference','request_key')
    @classmethod
    def nonempty(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('Supporting details cannot be blank')
        return value

    @model_validator(mode='after')
    def valid_basis(self):
        if self.period_end < self.period_start:
            raise ValueError('Invalid entitlement period')
        if self.leave_kind=='social':
            if self.period_start != date(self.period_start.year,1,1) or self.period_end != date(self.period_start.year,12,31):
                raise ValueError('Social leave uses a calendar year')
            grounds={item.strip() for item in self.social_grounds}
            if '' in grounds or len(grounds)!=len(self.social_grounds) or not grounds:
                raise ValueError('Document distinct social entitlement grounds')
            if self.social_days != (10 if len(grounds)==1 else 17):
                raise ValueError('Social days must match the documented grounds')
            if self.segments or self.excluded_service_dates:
                raise ValueError('Social leave is not prorated by annual service')
        elif not self.segments or self.social_days is not None or self.social_grounds:
            raise ValueError('Annual leave requires dated annual allowances')
        if self.leave_kind == 'additional':
            if not self.planned_work_dates or len(set(self.planned_work_dates)) != len(self.planned_work_dates):
                raise ValueError('Additional leave requires the documented full-year working calendar')
            if any(d < self.period_start or d > self.period_end for d in self.planned_work_dates):
                raise ValueError('Working calendar falls outside entitlement year')
            if set(self.planned_work_dates) & set(self.calendar_exclusions):
                raise ValueError('Working calendar conflicts with excluded calendar days')
        elif self.planned_work_dates:
            raise ValueError('Working-day proration applies only to additional leave')
        if self.leave_kind != 'annual' and any(s.disability_group is not None for s in self.segments):
            raise ValueError('Disability guarantee applies to basic annual leave')
        return self


class LeaveAccrualCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    through: date
    # Additional leave requires the actually documented qualifying work days.
    qualifying_dates: list[date] | None = Field(default=None,max_length=366)
    source_reference: str = Field(min_length=1,max_length=1000)
    request_key: str = Field(min_length=1,max_length=100)


class LeaveAllocationItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: int = Field(gt=0)
    bucket: Literal['before_2024','from_2024']
    days: Decimal = Field(gt=0,decimal_places=0)


class LeaveAllocationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allocations: list[LeaveAllocationItem] = Field(min_length=1,max_length=100)
    request_key: str = Field(min_length=1,max_length=100)
    source_reference: str = Field(min_length=1,max_length=1000)


class LeaveAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    employment_contract_id: int
    leave_kind: str
    entitlement_code: str
    period_start: date
    period_end: date
    accrual_start: date
    opening_id: int | None
    basis: dict
    source_reference: str


class LeaveBalanceEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    account_id: int
    kind: str
    effective_date: date
    acquisition_bucket: str
    days: Decimal
    leave_request_id: int | None
    termination_event_id: int | None
    reversal_of_id: int | None
    source_reference: str


class LeaveBalanceRead(BaseModel):
    account_id: int
    leave_kind: str
    period_start: date
    period_end: date
    before_2024: Decimal
    from_2024: Decimal
