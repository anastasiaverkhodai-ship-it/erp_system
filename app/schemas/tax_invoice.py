from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutputTaxInvoiceCreate(BaseModel):
    source_kind: Literal[
        "fulfillment",
        "settlement",
        "order_advance",
    ]
    source_id: int = Field(gt=0)
    document_number: str = Field(
        min_length=1,
        max_length=120,
    )


class InputTaxInvoiceLineCreate(BaseModel):
    line_number: int = Field(gt=0)
    claim_id: int = Field(gt=0)
    description: str = Field(
        min_length=1,
        max_length=500,
    )
    quantity: Decimal = Field(gt=0)
    uom_code: str = Field(
        min_length=1,
        max_length=20,
    )
    classification_kind: Literal[
        "uktzed",
        "dkpp",
    ]
    statutory_code: str = Field(
        min_length=1,
        max_length=32,
    )


class InputTaxInvoiceCreate(BaseModel):
    lines: list[
        InputTaxInvoiceLineCreate
    ] = Field(
        min_length=1,
        max_length=1000,
    )


class TaxInvoiceRegistrationCreate(BaseModel):
    status: Literal[
        "prepared",
        "submitted",
        "registered",
        "suspended",
        "rejected",
    ]
    event_date: date
    reference: str | None = Field(
        default=None,
        max_length=500,
    )


class TaxInvoiceResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    company_id: int
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
    source_kind: str
    created_by: int
    created_at: datetime


class TaxInvoiceRegistrationResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    company_id: int
    tax_invoice_id: int
    status: str
    event_date: date
    reference: str | None
    created_by: int
    created_at: datetime
