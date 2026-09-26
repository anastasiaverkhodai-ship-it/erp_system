from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.fixed_asset_acquisition import (
    FixedAssetAcquisitionCostType,
)


class FixedAssetAcquisitionCostCreate(BaseModel):
    request_key: str = Field(min_length=1, max_length=100)
    cost_type: FixedAssetAcquisitionCostType
    recognition_date: date
    amount: Decimal = Field(gt=0, decimal_places=2)
    source_journal_entry_line_id: int = Field(gt=0)
    source_description: str | None = Field(
        default=None,
        max_length=255,
    )


class FixedAssetAcquisitionCostReverse(BaseModel):
    reversal_date: date


class FixedAssetAcquisitionCostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    request_key: str
    cost_type: FixedAssetAcquisitionCostType
    recognition_date: date
    amount: Decimal
    source_journal_entry_line_id: int
    source_description: str | None
    reversal_of_id: int | None
    created_by: int
    created_at: datetime
