from datetime import date, datetime

from pydantic import BaseModel, Field


class AccountingPeriodCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class AccountingPeriodResponse(BaseModel):
    id: int
    company_id: int
    year: int
    month: int
    start_date: date
    end_date: date
    status: str
    is_locked: bool
    created_at: datetime
    closed_at: datetime | None

    model_config = {
        "from_attributes": True
    }