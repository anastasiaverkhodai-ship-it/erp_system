from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class IncomeDateAmount(BaseModel):
    model_config=ConfigDict(extra='forbid')
    income_date: date
    amount: Decimal = Field(max_digits=18,decimal_places=2,allow_inf_nan=False)

class IncomeDateRequestCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    line_id: int = Field(gt=0)
    request_key: str = Field(min_length=1,max_length=90)
    document_reference: str = Field(min_length=10,max_length=1000)
    dates: list[IncomeDateAmount] = Field(min_length=1,max_length=31)

    @model_validator(mode='after')
    def distinct_dates(self):
        if len({item.income_date for item in self.dates}) != len(self.dates):
            raise ValueError('Income dates must be distinct')
        return self
