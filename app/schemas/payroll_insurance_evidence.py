from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class InsuranceEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    employee_id: int = Field(gt=0)
    case_start: date
    total_service_months: int = Field(ge=0, strict=True)
    service_days: int = Field(default=0, ge=0, le=29, strict=True)
    recent_service_months: Decimal = Field(ge=0, le=12, max_digits=6, decimal_places=3)
    entitlement: Literal['standard','chornobyl_1_3','veteran','rehabilitated'] = 'standard'
    entitlement_reference: str | None = Field(default=None, min_length=1, max_length=500)
    document_reference: str = Field(min_length=1, max_length=500)
    issued_on: date
    request_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def validate_service(self):
        if self.recent_service_months > self.total_service_months + Decimal(self.service_days)/30:
            raise ValueError('Recent insurance service cannot exceed total service')
        if self.entitlement != 'standard' and not self.entitlement_reference:
            raise ValueError('A full-rate entitlement requires supporting evidence')
        return self


class InsuranceEvidenceReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: bool


class InsuranceEvidenceRead(InsuranceEvidenceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    status: str
    created_by: int
    created_at: datetime
    reviewed_by: int | None
    reviewed_at: datetime | None


class SickHistoryCalculationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    insurance_evidence_id: int = Field(gt=0)
