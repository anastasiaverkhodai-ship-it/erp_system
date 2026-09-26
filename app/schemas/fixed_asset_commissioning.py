from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class FixedAssetCommissioningCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=100)
    commissioning_date: date


class FixedAssetCommissioningReverse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reversal_date: date


class FixedAssetCommissioningRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    request_key: str
    commissioning_date: date
    reversal_of_id: int | None
    created_by: int
    created_at: datetime
