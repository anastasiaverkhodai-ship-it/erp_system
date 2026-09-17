from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, Field


class RegisterTotals(BaseModel):
    taxable_base: Decimal
    tax_amount: Decimal


class ControlTotals(BaseModel):
    taxable_base: Decimal
    expected_vat: Decimal
    posted_vat: Decimal
    difference: Decimal


class VatRegisterReport(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    as_of: datetime
    account_roles: dict[str, int]
    account_basis: Literal['current_company_profile']
    register_rows: list[dict[str, Any]] = Field(alias="register")
    register_totals: dict[str, RegisterTotals]
    events: list[dict[str, Any]]
    totals: dict[str, ControlTotals]
    rate_totals: list[dict[str, Any]]
    unclassified_tax_settlement: Decimal
    declaration: dict[str, Any] | None
    matched: bool
    issues: list[dict[str, Any]]
