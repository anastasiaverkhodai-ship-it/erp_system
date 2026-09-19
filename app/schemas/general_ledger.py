from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class GeneralLedgerLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    journal_entry_id: int
    journal_entry_line_id: int
    entry_date: date
    line_no: int
    account_id: int
    account_code: str
    account_name: str
    description: str | None = None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class GeneralLedgerReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    date_from: date
    date_to: date
    account_id: int | None = None
    account_code: str | None = None
    opening_balance: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_balance: Decimal
    lines: list[GeneralLedgerLine]


class AccountCardReport(GeneralLedgerReport):
    account_id: int
    account_code: str
    account_name: str
    normal_balance: str
