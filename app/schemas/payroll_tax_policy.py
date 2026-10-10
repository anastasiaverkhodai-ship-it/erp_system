from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Decimal

class DatedPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    effective_from: date
    effective_to: date | None=None
    @model_validator(mode='after')
    def dates(self):
        if self.effective_to and self.effective_to<self.effective_from:
            raise ValueError('Invalid effective date range')
        return self

class TaxProfileCreate(DatedPolicy):
    tax_evidence_id: int | None = Field(default=None, gt=0)
    employment_contract_id: int=Field(gt=0)
    category: Literal['standard','benefit_eligible','exempt']='standard'
    benefit_code: str | None=Field(default=None,min_length=1,max_length=64)
    exemption_code: str | None=Field(default=None,min_length=1,max_length=64)
    benefit_amount_override: Money | None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    benefit_income_limit_override: Money | None=Field(default=None,gt=0,max_digits=18,decimal_places=2)
    @model_validator(mode='after')
    def entitlement(self):
        if self.category=='benefit_eligible' and not self.benefit_code:
            raise ValueError('Benefit requires a supporting code/reference')
        if self.category=='exempt' and not self.exemption_code:
            raise ValueError('Exemption requires a supporting code/reference')
        overrides=(self.benefit_amount_override,self.benefit_income_limit_override)
        if (overrides[0] is None)!=(overrides[1] is None):
            raise ValueError('Specify both benefit override values')
        if overrides[0] is not None and self.category!='benefit_eligible':
            raise ValueError('Benefit overrides require a benefit profile')
        return self

class TaxProfileRead(BaseModel):
    # Creation validation must not make legacy, immutable profiles unreadable.
    model_config=ConfigDict(from_attributes=True)
    tax_evidence_id: int | None = None
    id:int
    company_id:int
    employee_id:int
    employment_contract_id:int
    category:str
    benefit_code:str | None
    exemption_code:str | None
    benefit_amount_override:Decimal | None
    benefit_income_limit_override:Decimal | None
    effective_from:date
    effective_to:date | None

class TaxBaseRuleCreate(DatedPolicy):
    component: Literal['personal_income_tax','military_levy','unified_social_contribution']
    employment_kind: Literal['primary','internal_secondary','external_secondary'] | None=None
    tax_profile_category: Literal['standard','benefit_eligible','exempt'] | None=None
    base_mode: Literal['gross','gross_after_benefit']='gross'
    benefit_amount: Money=Field(default=Decimal(0),ge=0,max_digits=18,decimal_places=2)
    benefit_income_limit: Money | None=Field(default=None,gt=0,max_digits=18,decimal_places=2)
    minimum_base_amount: Money | None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    maximum_base_amount: Money | None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    exemption_applies: bool=False
    rule_code: str=Field(min_length=1,max_length=100)
    rule_version: str=Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def controls(self):
        if self.base_mode=='gross_after_benefit':
            if self.component!='personal_income_tax' or self.benefit_income_limit is None:
                raise ValueError('PIT benefit requires an explicit monthly income limit')
        elif self.benefit_amount or self.benefit_income_limit is not None:
            raise ValueError('Benefit values require gross_after_benefit mode')
        if self.minimum_base_amount is not None or self.maximum_base_amount is not None:
            if self.component!='unified_social_contribution':
                raise ValueError('Minimum and maximum bases apply to USC only')
        if self.minimum_base_amount is not None and self.maximum_base_amount is not None and self.minimum_base_amount>self.maximum_base_amount:
            raise ValueError('Minimum base exceeds maximum')
        if self.exemption_applies and self.tax_profile_category!='exempt':
            raise ValueError('Exemption rule requires an exempt profile selector')
        return self

class TaxBaseRuleRead(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int
    company_id:int
    component:str
    employment_kind:str | None
    tax_profile_category:str | None
    base_mode:str
    benefit_amount:Decimal
    benefit_income_limit:Decimal | None
    minimum_base_amount:Decimal | None
    maximum_base_amount:Decimal | None
    exemption_applies:bool
    rule_code:str
    rule_version:str
    effective_from:date
    effective_to:date | None


class TaxPolicyEnd(BaseModel):
    model_config = ConfigDict(extra='forbid')
    effective_to: date
