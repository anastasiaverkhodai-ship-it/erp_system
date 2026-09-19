from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class TrialBalanceLine(BaseModel):
    account_id: int
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str

    opening_debit: Decimal
    opening_credit: Decimal

    period_debit: Decimal
    period_credit: Decimal

    closing_debit: Decimal
    closing_credit: Decimal


class TrialBalanceReport(BaseModel):
    company_id: int
    date_from: date
    date_to: date

    lines: list[TrialBalanceLine]

    total_opening_debit: Decimal
    total_opening_credit: Decimal

    total_period_debit: Decimal
    total_period_credit: Decimal

    total_closing_debit: Decimal
    total_closing_credit: Decimal
