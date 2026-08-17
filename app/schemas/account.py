from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=255)
    account_type: str = Field(min_length=1, max_length=50)
    parent_id: int | None = None


class AccountUpdate(BaseModel):
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    account_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    parent_id: int | None = None
    is_active: bool | None = None


class AccountResponse(BaseModel):
    id: int
    company_id: int
    code: str
    name: str
    account_type: str
    parent_id: int | None
    is_active: bool
    created_at: datetime

    model_config = {
        "from_attributes": True
    }