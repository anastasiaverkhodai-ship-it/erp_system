from datetime import date, datetime
from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


class VatDeclarationBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reporting_year: int = Field(ge=2000, le=9999)
    reporting_month: int = Field(ge=1, le=12)
    source_cutoff_at: AwareDatetime


class VatDeclarationLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["finalized", "submitted", "accepted", "rejected"]
    event_date: date
    reference: str | None = Field(default=None, max_length=500)


class VatDeclarationSourceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_number: int
    source_kind: str
    economic_effective_date: date
    direction: str
    taxable_base_delta: Decimal
    tax_amount_delta: Decimal
    currency_code: str
    source_reversal_of_id: int | None
    tax_rate_code: str | None
    tax_rate: Decimal | None


class VatDeclarationCarryForwardLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_declaration_id: int
    origin_reporting_year: int
    origin_reporting_month: int
    opening_amount: Decimal
    consumed_amount: Decimal
    closing_amount: Decimal


class VatDeclarationStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    event_date: date
    reference: str | None
    created_by: int
    created_at: datetime


class VatDeclarationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    reporting_year: int
    reporting_month: int
    period_start: date
    period_end: date
    source_cutoff_at: datetime
    snapshot_version: int
    supersedes_declaration_id: int | None

    output_taxable_base: Decimal
    output_vat: Decimal
    input_taxable_base: Decimal
    input_vat_credit: Decimal

    opening_negative_carry: Decimal
    vat_payable: Decimal
    current_period_negative: Decimal
    closing_negative_carry: Decimal

    currency_code: str
    created_by: int
    created_at: datetime


class VatDeclarationDetailRead(VatDeclarationRead):
    source_lines: list[VatDeclarationSourceLineRead]
    carry_forward_lines: list[VatDeclarationCarryForwardLineRead]
    status_events: list[VatDeclarationStatusEventRead]
