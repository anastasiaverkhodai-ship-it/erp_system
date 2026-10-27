from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SickCorrectionApply(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    original_sick_id: int = Field(gt=0)
    insurance_evidence_id: int = Field(gt=0)
    correction_date: date
    request_key: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=10,max_length=2000)


class SickCorrectionReturnCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    calculation_id: int = Field(gt=0)
    journal_entry_id: int = Field(gt=0)
    bank_account_id: int = Field(gt=0)
    kind: Literal['employee','insurer']
    request_key: str = Field(min_length=1,max_length=100)
    document_reference: str = Field(min_length=10,max_length=500)
