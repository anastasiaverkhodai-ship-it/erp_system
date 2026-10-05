from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_reference: str=Field(min_length=1,max_length=500)

    @field_validator('source_reference')
    @classmethod
    def reference(cls,value):
        value=value.strip()
        if not value: raise ValueError('Source reference cannot be blank')
        return value


class PayrollOpeningDebtInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    employee_id: int=Field(gt=0)
    net_amount: Decimal=Field(max_digits=18,decimal_places=2)
    @field_validator('net_amount')
    @classmethod
    def nonzero(cls,value):
        if not value.is_finite() or value==0: raise ValueError('A finite nonzero amount is required')
        return value


class PayrollOpeningAttach(SourceInput):
    request_key: str=Field(min_length=1,max_length=200)
    debts: list[PayrollOpeningDebtInput]=Field(min_length=1,max_length=10000)
    @field_validator('request_key')
    @classmethod
    def key(cls,value):
        value=value.strip()
        if not value: raise ValueError('Request key cannot be blank')
        return value
    @model_validator(mode='after')
    def unique_employees(self):
        if len({r.employee_id for r in self.debts})!=len(self.debts):
            raise ValueError('Each employee must occur once')
        return self


class PayrollEarningsHistoryInput(SourceInput):
    month: date
    cutover_date: date
    gross_amount: Decimal=Field(ge=0,max_digits=18,decimal_places=2)
    vacation_earnings: Decimal=Field(ge=0,max_digits=18,decimal_places=2)
    sick_earnings: Decimal=Field(ge=0,max_digits=18,decimal_places=2)
    vacation_days: int=Field(ge=0,le=31)
    sick_days: int=Field(ge=0,le=31)
    @model_validator(mode='after')
    def calendar(self):
        days=monthrange(self.month.year,self.month.month)[1]
        end=self.month+timedelta(days=days-1)
        if self.month.day!=1 or end>=self.cutover_date:
            raise ValueError('Historical month must be complete and precede cutover')
        if self.vacation_days>days or self.sick_days>days:
            raise ValueError('Eligible days exceed calendar month')
        if (self.vacation_earnings>0 and self.vacation_days==0) or (self.sick_earnings>0 and self.sick_days==0):
            raise ValueError('Eligible earnings require eligible days')
        return self


class PayrollLeaveOpeningInput(SourceInput):
    leave_type: Literal['annual','additional','social']='annual'
    working_year_start: date
    working_year_end: date
    as_of: date
    remaining_days: Decimal=Field(ge=0,max_digits=10,decimal_places=2)
    @model_validator(mode='after')
    def dates(self):
        if self.working_year_end<self.working_year_start or self.as_of<self.working_year_start:
            raise ValueError('Invalid working year or opening date')
        return self


class PayrollEarningsHistoryRead(PayrollEarningsHistoryInput):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    employment_contract_id: int


class PayrollLeaveOpeningRead(PayrollLeaveOpeningInput):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    employment_contract_id: int


class PayrollOpeningDebtRead(PayrollOpeningDebtInput):
    model_config=ConfigDict(from_attributes=True)
    id: int
    package_id: int
    company_id: int
    currency_code: str
    as_of: date
