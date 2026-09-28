from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.fixed_asset_repair_improvement import (
    FixedAssetRepairImprovementType,
)


class FixedAssetRepairImprovementCreate(BaseModel):
    operation_type: FixedAssetRepairImprovementType
    operation_date: date
    amount: Decimal = Field(gt=0)
    source_journal_entry_line_id: int
    request_key: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class FixedAssetRepairImprovementReverse(BaseModel):
    reversal_date: date
    request_key: str = Field(min_length=1, max_length=100)


class FixedAssetRepairImprovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    operation_type: FixedAssetRepairImprovementType
    operation_date: date
    amount: Decimal
    source_journal_entry_line_id: int
    request_key: str
    description: str | None
    journal_entry_id: int | None
    reversal_of_id: int | None
    created_by: int
    created_at: datetime
