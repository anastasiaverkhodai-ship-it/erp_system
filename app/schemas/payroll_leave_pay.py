from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LeaveHistoryCalculationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference_period_start: date | None = None
    reference_period_end: date | None = None


class LeaveAverageSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_type: str
    source_id: int
    source_period_start: date | None
    source_period_end: date | None
    earnings_amount: Decimal
    eligible_days: Decimal
    source_reference: str | None


class LeaveAverageHistoryRead(BaseModel):
    reference_period_start: date
    reference_period_end: date
    company_id: int
    leave_request_id: int
    employment_contract_id: int
    purpose: Literal['vacation', 'sick']
    currency_code: str
    eligible_earnings: Decimal
    eligible_days: Decimal
    average_daily_amount: Decimal
    sources: list[LeaveAverageSourceRead]


class VacationCalculationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    leave_request_id: int
    employment_contract_id: int
    reference_period_start: date
    reference_period_end: date
    eligible_earnings: Decimal
    eligible_days: Decimal
    leave_days: Decimal
    average_daily_amount: Decimal
    vacation_pay_amount: Decimal
    currency_code: str
    rule_code: str
    rule_version: str
    sources: list[LeaveAverageSourceRead] = Field(default_factory=list)
