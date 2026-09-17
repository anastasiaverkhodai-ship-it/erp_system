from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class OutputTaxInvoiceCorrectionLineCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    line_number: int = Field(
        gt=0
    )

    source_kind: Literal[
        "sales_return",
        "sales_value_correction",
        "recognition_reversal",
    ]

    source_id: int = Field(
        gt=0
    )

    reason_code: str = Field(
        min_length=1,
        max_length=40,
    )


class InputTaxInvoiceCorrectionLineCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    line_number: int = Field(
        gt=0
    )

    source_kind: Literal[
        "purchase_return",
        "purchase_value_correction",
    ]

    source_id: int = Field(
        gt=0
    )

    reason_code: str = Field(
        min_length=1,
        max_length=40,
    )

    tax_credit_evidence_id: int | None = Field(
        default=None,
        gt=0,
    )


class OutputTaxInvoiceCorrectionCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    request_key: str = Field(
        min_length=1,
        max_length=255,
    )

    original_tax_invoice_id: int = Field(
        gt=0
    )

    document_number: str = Field(
        min_length=1,
        max_length=120,
    )

    document_date: date

    lines: list[
        OutputTaxInvoiceCorrectionLineCreate
    ] = Field(
        min_length=1,
        max_length=500,
    )


class InputTaxInvoiceCorrectionCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    request_key: str = Field(
        min_length=1,
        max_length=255,
    )

    original_tax_invoice_id: int = Field(
        gt=0
    )

    document_number: str = Field(
        min_length=1,
        max_length=120,
    )

    document_date: date

    registered_on: date

    receipt_reference: str = Field(
        min_length=1,
        max_length=500,
    )

    lines: list[
        InputTaxInvoiceCorrectionLineCreate
    ] = Field(
        min_length=1,
        max_length=500,
    )


class TaxInvoiceCorrectionRegistrationCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    request_key: str = Field(
        min_length=1,
        max_length=255,
    )

    status: Literal[
        "submitted",
        "registered",
        "suspended",
        "rejected",
    ]

    event_date: date

    reference: str = Field(
        min_length=1,
        max_length=500,
    )


class TaxInvoiceCorrectionResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    company_id: int
    original_tax_invoice_id: int
    direction: str
    document_number: str
    document_date: date
    currency_code: str
    seller_name: str
    seller_tax_number: str
    seller_vat_number: str
    buyer_name: str | None
    buyer_tax_number: str | None
    buyer_vat_number: str | None
    created_by: int
    created_at: datetime


class TaxInvoiceCorrectionLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    company_id: int
    tax_invoice_correction_id: int
    line_number: int
    original_tax_invoice_line_id: int
    source_kind: str
    sales_return_recognition_event_id: int | None
    trade_value_correction_event_id: int | None
    purchase_return_vat_adjustment_event_id: int | None
    purchase_value_correction_vat_adjustment_event_id: int | None
    tax_recognition_reversal_event_id: int | None
    tax_credit_evidence_id: int | None
    reason_code: str
    description: str
    uom_code: str
    classification_kind: str
    statutory_code: str
    tax_rate_code: str
    tax_rate: Decimal
    quantity_delta: Decimal
    unit_price_without_vat_delta: Decimal
    taxable_base_delta: Decimal
    tax_amount_delta: Decimal
    total_with_vat_delta: Decimal


class TaxInvoiceCorrectionRegistrationResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    company_id: int
    tax_invoice_correction_id: int
    status: str
    event_date: date
    reference: str | None
    created_by: int
    created_at: datetime


class TaxInvoiceCorrectionDetailResponse(BaseModel):
    header: TaxInvoiceCorrectionResponse
    lines: list[
        TaxInvoiceCorrectionLineResponse
    ]
    registration_events: list[
        TaxInvoiceCorrectionRegistrationResponse
    ]
