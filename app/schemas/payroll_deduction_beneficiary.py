from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from app.services.employee_bank_details import normalize_employee_iban


class DeductionBeneficiaryCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=100)
    recipient_name: str = Field(min_length=1, max_length=255)
    recipient_identifier: str = Field(min_length=1, max_length=100)
    settlement_kind: Literal['recipient_payment', 'employer_recovery']
    iban: str | None = None
    account_id: int = Field(gt=0)
    document_reference: str = Field(min_length=1, max_length=500)
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode='after')
    def validate_details(self):
        self.iban = normalize_employee_iban(self.iban)
        if (self.settlement_kind == 'recipient_payment') != (self.iban is not None):
            raise ValueError('Recipient payment requires an IBAN; employer recovery must not have one')
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError('Invalid validity interval')
        return self


class DeductionBeneficiaryRead(DeductionBeneficiaryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    instruction_id: int
    status: str
    created_by: int
    created_at: datetime
    verified_by: int | None
    verified_at: datetime | None
    revoked_by: int | None
    revoked_at: datetime | None
