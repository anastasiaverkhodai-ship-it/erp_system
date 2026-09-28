from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.fixed_asset_revaluation_impairment import (
    FixedAssetRevaluationImpairmentType,
)


class FixedAssetRevaluationImpairmentCreate(BaseModel):
    operation_type: FixedAssetRevaluationImpairmentType
    operation_date: date
    carrying_amount_after: Decimal = Field(ge=0)
    counterpart_account_id: int = Field(gt=0)
    request_key: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class FixedAssetRevaluationImpairmentReverse(BaseModel):
    reversal_date: date
    request_key: str = Field(min_length=1, max_length=100)


class FixedAssetRevaluationImpairmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    operation_type: FixedAssetRevaluationImpairmentType
    operation_date: date
    carrying_amount_before: Decimal
    carrying_amount_after: Decimal
    amount: Decimal
    accumulated_depreciation: Decimal
    original_cost_before: Decimal
    original_cost_after: Decimal
    counterpart_account_id: int
    request_key: str
    description: str | None
    journal_entry_id: int | None
    reversal_of_id: int | None
    created_by: int
    created_at: datetime
