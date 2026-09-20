from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class AccountingControlFamilyResult(BaseModel):
    family: Literal[
        "ar",
        "ap",
        "cash_bank",
        "inventory",
        "input_vat",
        "output_vat",
    ]
    status: Literal[
        "matched",
        "mismatch",
        "not_implemented",
    ]
    expected_amount: Decimal | None = None
    posted_amount: Decimal | None = None
    difference: Decimal | None = None
    issue_count: int = 0
    note: str | None = None


class ConsolidatedAccountingControlReport(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    matched: bool
    implemented_family_count: int
    not_implemented_family_count: int
    families: list[AccountingControlFamilyResult]
