from datetime import datetime

from pydantic import BaseModel, Field

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)


class AccountCreate(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=20,
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    account_type: AccountType

    normal_balance: AccountNormalBalance

    parent_id: int | None = None

    is_postable: bool = True


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

    account_type: AccountType | None = None

    normal_balance: AccountNormalBalance | None = None

    parent_id: int | None = None

    is_postable: bool | None = None

    is_active: bool | None = None


class AccountResponse(BaseModel):
    id: int

    company_id: int

    code: str

    name: str

    account_type: AccountType

    normal_balance: AccountNormalBalance

    parent_id: int | None

    is_postable: bool

    is_system: bool

    is_active: bool

    created_at: datetime

    model_config = {
        "from_attributes": True,
    }
