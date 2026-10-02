from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.leave_request import (
    LeaveRequestStatus,
    LeaveType,
)


class LeaveRequestCreate(BaseModel):
    employment_contract_id: int
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str | None = Field(
        default=None,
        max_length=4000,
    )

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.end_date < self.start_date:
            raise ValueError(
                "end_date must be on or after start_date"
            )
        return self


class LeaveRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employment_contract_id: int
    leave_type: LeaveType
    start_date: date
    end_date: date
    status: LeaveRequestStatus
    reason: str | None

    requested_by: int

    approved_by: int | None
    approved_at: datetime | None

    rejected_by: int | None
    rejected_at: datetime | None

    cancelled_by: int | None
    cancelled_at: datetime | None

    created_at: datetime
    updated_at: datetime


class LeaveRequestReject(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=4000,
    )


class LeaveRequestCancel(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=4000,
    )
