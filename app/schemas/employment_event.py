from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.employment_contract import EmploymentKind, WorkArrangement


class EmploymentEventCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_type: Literal['hire','transfer','termination']
    effective_date: date
    order_number: str = Field(min_length=1,max_length=100)
    order_date: date
    reason: str = Field(min_length=1,max_length=1000)
    request_key: str = Field(min_length=1,max_length=200)
    department_id: int | None = Field(default=None,gt=0)
    position_id: int | None = Field(default=None,gt=0)
    employment_kind: EmploymentKind | None = None
    work_arrangement: WorkArrangement | None = None

    @field_validator('order_number','reason','request_key')
    @classmethod
    def nonempty(cls, value):
        value=value.strip()
        if not value:
            raise ValueError('Document details cannot be blank')
        return value

    @model_validator(mode='after')
    def check_fields(self):
        terms={'department_id','position_id','employment_kind','work_arrangement'} & self.model_fields_set
        if self.event_type != 'transfer' and terms:
            raise ValueError('Only transfer events change employment terms')
        if self.event_type == 'transfer' and not terms:
            raise ValueError('A transfer must change employment terms')
        if any(field in self.model_fields_set and getattr(self,field) is None
               for field in ('employment_kind','work_arrangement')):
            raise ValueError('Employment kind and work arrangement cannot be null')
        return self


class EmploymentEventRead(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: int
    company_id: int
    employment_contract_id: int
    event_type: str
    effective_date: date
    order_number: str
    order_date: date
    reason: str
    request_key: str
    before_state: dict
    after_state: dict
    reversal_of_id: int | None
    created_by: int
    created_at: datetime


class EmploymentEventReverse(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_number: str = Field(min_length=1,max_length=100)
    order_date: date
    reason: str = Field(min_length=1,max_length=1000)
    request_key: str = Field(min_length=1,max_length=200)

    @field_validator('order_number','reason','request_key')
    @classmethod
    def nonempty(cls, value):
        value=value.strip()
        if not value:
            raise ValueError('Document details cannot be blank')
        return value


class EmploymentEventBatchItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    contract_id: int = Field(gt=0)
    event: EmploymentEventCreate


class EmploymentEventBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    events: list[EmploymentEventBatchItem] = Field(min_length=1,max_length=100)
