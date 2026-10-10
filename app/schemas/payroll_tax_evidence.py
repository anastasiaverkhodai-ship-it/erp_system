"""API schemas for employee payroll tax evidence."""

from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class TaxEvidenceCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    employee_id: int = Field(gt=0)

    document_type: str = Field(
        min_length=1,
        max_length=100,
    )

    document_number: str = Field(
        min_length=1,
        max_length=150,
    )

    issued_on: date
    valid_from: date
    valid_to: date | None = None

    entitlement_type: Literal[
        "benefit",
        "exemption",
        "individual_rate",
    ]

    entitlement_code: str = Field(
        min_length=1,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_dates(self):
        if self.issued_on > self.valid_from:
            raise ValueError(
                "Issue date cannot exceed validity start"
            )

        if (
            self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError(
                "Invalid evidence validity window"
            )

        return self


class TaxEvidenceVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool


class TaxEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employee_id: int

    document_type: str
    document_number: str

    issued_on: date
    valid_from: date
    valid_to: date | None

    entitlement_type: str
    entitlement_code: str

    verification_status: str
    verified_by: int | None
    verified_at: datetime | None

    created_by: int
    created_at: datetime
    updated_at: datetime
