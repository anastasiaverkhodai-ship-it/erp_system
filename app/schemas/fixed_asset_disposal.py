from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FixedAssetDisposalCreate(BaseModel):
    disposal_date: date
    disposal_account_id: int
    request_key: str = Field(
        min_length=1,
        max_length=100,
    )
    description: str | None = None


class FixedAssetDisposalReverse(BaseModel):
    reversal_date: date
    request_key: str = Field(
        min_length=1,
        max_length=100,
    )


class FixedAssetDisposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    disposal_date: date

    original_cost: Decimal
    accumulated_depreciation: Decimal
    carrying_amount: Decimal
    salvage_value: Decimal

    previous_status: str
    disposal_account_id: int

    request_key: str
    description: str | None

    journal_entry_id: int | None
    reversal_of_id: int | None

    created_by: int
    created_at: datetime
