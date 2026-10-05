from datetime import date,datetime
from decimal import Decimal
from typing import Literal
from pydantic import ConfigDict,Field,model_validator,field_validator
from app.schemas.payroll_opening import SourceInput


class PayrollSupplementCreate(SourceInput):
    kind: Literal['bonus','holiday_bonus','hardship','night','overtime','rest_day','regular_extra']
    work_date: date
    minutes: int=Field(default=0,ge=0,le=1440)
    coefficient: Decimal=Field(default=Decimal('1'),ge=0,max_digits=10,decimal_places=6)
    amount: Decimal=Field(default=Decimal('0'),ge=0,max_digits=18,decimal_places=2)
    request_key: str=Field(min_length=1,max_length=200)

    @field_validator('request_key')
    @classmethod
    def key(cls,value):
        value=value.strip()
        if not value: raise ValueError('Request key cannot be blank')
        return value

    @model_validator(mode='after')
    def semantics(self):
        if self.kind=='night' and 'coefficient' not in self.model_fields_set:
            self.coefficient=Decimal('0.20')
        if self.kind in {'bonus','holiday_bonus','hardship'}:
            if self.amount<=0 or self.minutes!=0 or self.coefficient!=1:
                raise ValueError('Fixed supplement requires a positive amount, zero minutes and coefficient 1')
        else:
            if self.minutes<=0 or self.amount!=0:
                raise ValueError('Time supplement requires minutes; amount is derived from salary rate')
            if self.kind=='night' and self.coefficient<Decimal('0.20'):
                raise ValueError('Night premium must be at least 20 percent')
            if self.kind!='night' and self.coefficient!=1:
                raise ValueError('Use coefficient 1 for verified extra time; base salary already includes straight-time pay')
        return self


class PayrollSupplementRead(PayrollSupplementCreate):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    payroll_input_id: int
    cancelled_at: datetime | None
