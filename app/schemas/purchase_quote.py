from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4, allow_inf_nan=False)]
UnitPrice = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)]


class PurchaseQuoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    supplier_id: int = Field(gt=0)
    contract_id: int | None = Field(default=None, gt=0)
    product_id: int = Field(gt=0)
    reference: str = Field(min_length=1, max_length=100)
    currency_code: Literal["UAH"] = "UAH"
    min_quantity: Quantity
    max_quantity: Quantity | None = None
    unit_price_net: UnitPrice
    unit_price_gross: UnitPrice
    delivery_net: Money = Decimal("0")
    delivery_gross: Money = Decimal("0")
    lead_time_days: int = Field(ge=0, le=3650)
    payment_term_days: int = Field(ge=0, le=3650)
    valid_from: date
    valid_until: date

    @field_validator("reference")
    @classmethod
    def nonblank_reference(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Reference cannot be blank")
        return value

    @model_validator(mode="after")
    def consistent_terms(self):
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on/after valid_from")
        if self.max_quantity is not None and self.max_quantity < self.min_quantity:
            raise ValueError("max_quantity must be at least min_quantity")
        if self.unit_price_gross < self.unit_price_net or self.delivery_gross < self.delivery_net:
            raise ValueError("Gross amounts cannot be smaller than net amounts")
        return self


class PurchaseQuoteResponse(PurchaseQuoteCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    is_active: bool
    created_by: int
    created_at: datetime
    withdrawn_by: int | None
    withdrawn_at: datetime | None


class PurchaseQuoteComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    product_id: int = Field(gt=0)
    quantity: Quantity
    order_date: date
    required_delivery_date: date | None = None
    currency_code: Literal["UAH"] = "UAH"

    @model_validator(mode="after")
    def valid_deadline(self):
        if self.required_delivery_date is not None and self.required_delivery_date < self.order_date:
            raise ValueError("Delivery deadline cannot precede order date")
        return self


class RankedPurchaseQuote(BaseModel):
    quote_id: int
    supplier_id: int
    supplier_name: str
    reference: str
    contract_id: int | None
    goods_net: Decimal
    goods_gross: Decimal
    delivery_net: Decimal
    delivery_gross: Decimal
    total_net: Decimal
    total_vat: Decimal
    total_payable: Decimal
    delivery_date: date
    payment_term_days: int


class RejectedPurchaseQuote(BaseModel):
    quote_id: int
    reasons: list[str]


class PurchaseQuoteComparisonResponse(BaseModel):
    product_id: int
    quantity: Decimal
    currency_code: Literal["UAH"] = "UAH"
    criterion: Literal["total_payable_including_vat_and_delivery"] = "total_payable_including_vat_and_delivery"
    recommended_quote_id: int | None
    ranked: list[RankedPurchaseQuote]
    rejected: list[RejectedPurchaseQuote]
