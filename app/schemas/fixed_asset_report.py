"""Read-only historical fixed-asset reports (money is serialized as decimals)."""
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field


class FixedAssetReportIssue(BaseModel):
    code: str
    message: str
    fixed_asset_id: int | None = None
    event_type: str | None = None
    event_id: int | None = None
    journal_entry_id: int | None = None
    account_id: int | None = None


class FixedAssetMovementTotals(BaseModel):
    opening_cost: Decimal = Decimal('0.00')
    opening_accumulated: Decimal = Decimal('0.00')
    opening_net: Decimal = Decimal('0.00')
    cost_increase: Decimal = Decimal('0.00')
    cost_decrease: Decimal = Decimal('0.00')
    accumulated_increase: Decimal = Decimal('0.00')
    accumulated_decrease: Decimal = Decimal('0.00')
    closing_cost: Decimal = Decimal('0.00')
    closing_accumulated: Decimal = Decimal('0.00')
    closing_net: Decimal = Decimal('0.00')


class FixedAssetMovementLine(FixedAssetMovementTotals):
    fixed_asset_id: int
    asset_number: str
    current_name: str
    current_status: str
    asset_account_id: int
    accumulated_depreciation_account_id: int


class FixedAssetReportEvent(BaseModel):
    fixed_asset_id: int
    event_type: str
    event_id: int
    event_date: date
    reversal_of_id: int | None = None
    journal_entry_ids: list[int] = Field(default_factory=list)
    cost_change: Decimal = Decimal('0.00')
    accumulated_change: Decimal = Decimal('0.00')
    amount: Decimal = Decimal('0.00')
    period_start: date | None = None
    period_end: date | None = None
    method: str | None = None
    actual_output: Decimal | None = None
    sale_net_amount: Decimal | None = None


class FixedAssetMovementReport(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    lines: list[FixedAssetMovementLine]
    totals: FixedAssetMovementTotals
    events: list[FixedAssetReportEvent]
    issues: list[FixedAssetReportIssue]
    reconciled: bool


class FixedAssetGLBalance(BaseModel):
    opening: Decimal = Decimal('0.00')  # debit minus credit
    period_debit: Decimal = Decimal('0.00')
    period_credit: Decimal = Decimal('0.00')
    closing: Decimal = Decimal('0.00')


class FixedAssetGLLine(BaseModel):
    account_id: int
    account_code: str
    account_name: str
    expected: FixedAssetGLBalance
    actual: FixedAssetGLBalance
    difference: FixedAssetGLBalance  # actual minus expected, all four columns
    matched: bool


class FixedAssetGLReconciliation(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    lines: list[FixedAssetGLLine]
    issues: list[FixedAssetReportIssue]
    matched: bool


class FixedAssetDepreciationReport(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    events: list[FixedAssetReportEvent]
    charged: Decimal
    reversed: Decimal
    net: Decimal
    issues: list[FixedAssetReportIssue]
    reconciled: bool
