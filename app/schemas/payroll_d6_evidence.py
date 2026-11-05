from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator


class D6Interval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    start_date:date
    end_date:date
    basis_reference:str=Field(min_length=10,max_length=500)
    basis_code_candidate:str|None=Field(default=None,min_length=1,max_length=30)
    unit:Literal['days','hours']
    quantity:Decimal=Field(gt=0,max_digits=10,decimal_places=3,allow_inf_nan=False)
    normal_quantity:Decimal=Field(gt=0,max_digits=10,decimal_places=3,allow_inf_nan=False)

    @model_validator(mode='after')
    def valid_interval(self):
        if self.end_date<self.start_date:raise ValueError('Reversed special-service interval')
        maximum=Decimal((self.end_date-self.start_date).days+1)*(24 if self.unit=='hours' else 1)
        if self.quantity>maximum:
            raise ValueError('Duration exceeds the documented calendar interval')
        return self


class D6EvidenceCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    payroll_period_id:int=Field(gt=0,strict=True)
    employment_contract_id:int=Field(gt=0,strict=True)
    supersedes_id:int|None=Field(default=None,gt=0,strict=True)
    applies:bool=Field(strict=True)
    document_date:date
    document_reference:str=Field(min_length=10,max_length=500)
    intervals:list[D6Interval]=Field(default_factory=list,max_length=100)
    request_key:str=Field(min_length=8,max_length=120)

    @model_validator(mode='after')
    def applicable_evidence(self):
        if self.applies != bool(self.intervals):
            raise ValueError('Applicable service needs intervals; nonapplicability must have none')
        identities=[(x.start_date,x.end_date,x.basis_reference,x.basis_code_candidate) for x in self.intervals]
        if len(set(identities))!=len(identities):raise ValueError('Duplicate service interval')
        # Multiple legal grounds may describe the same calendar. Do not merge
        # or sum them as if that established statutory special-service codes.
        return self


class D6EvidenceReviewCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    approved:bool=Field(strict=True)
    review_reference:str=Field(min_length=10,max_length=500)
