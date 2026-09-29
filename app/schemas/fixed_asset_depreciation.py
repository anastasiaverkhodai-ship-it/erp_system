from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FixedAssetDepreciationCreate(BaseModel):
    fixed_asset_id: int
    request_key: str = Field(
        min_length=1,
        max_length=100,
    )
    period_start: date
    period_end: date
    posting_date: date
    actual_output: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.period_end < self.period_start:
            raise ValueError(
                "period_end cannot precede period_start"
            )

        if self.posting_date < self.period_end:
            raise ValueError(
                "posting_date cannot precede period_end"
            )

        return self


class FixedAssetDepreciationReverse(BaseModel):
    reversal_date: date


class FixedAssetDepreciationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    request_key: str
    period_start: date
    period_end: date
    posting_date: date
    actual_output: Decimal | None
    expected_output: Decimal | None
    method: str
    amount: Decimal
    accumulated_before: Decimal
    accumulated_after: Decimal
    reversal_of_id: int | None
    created_by: int
    created_at: datetime
