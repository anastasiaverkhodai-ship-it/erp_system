from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PurchaseLandedCostCreate(BaseModel):
    source_journal_entry_line_id: int = Field(gt=0)
    request_key: str = Field(min_length=1, max_length=100)
    trade_document_id: int = Field(gt=0)
    warehouse_document_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    cost_date: date
    reason_code: str | None = Field(default=None, min_length=1, max_length=50)


class PurchaseLandedCostReverse(BaseModel):
    reversal_date: date
    reason_code: str | None = Field(default=None, min_length=1, max_length=50)


class PurchaseLandedCostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    trade_document_id: int
    warehouse_document_id: int
    amount: Decimal
    currency_code: str
    cost_date: date
    reversal_of_id: int | None
