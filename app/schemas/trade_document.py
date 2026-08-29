from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.currency_catalog_service import (
    CurrencyNotFoundError,
    SYSTEM_CURRENCY_CATALOG,
)
from app.services.tax_price_types import (
    TaxPriceMode,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


def _normalize_currency_code(
    value: Any,
) -> Any:
    if not isinstance(value, str):
        return value

    code = value.strip().upper()

    try:
        SYSTEM_CURRENCY_CATALOG.get(
            code
        )
    except CurrencyNotFoundError as exc:
        raise ValueError(
            f"Unknown currency code: {code}"
        ) from exc

    return code


class TradeDocumentLineCreate(BaseModel):
    product_id: int = Field(
        gt=0,
    )

    warehouse_id: int | None = Field(
        default=None,
        gt=0,
    )

    quantity: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
    )

    unit_price: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=4,
    )

    tax_rate_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )

    tax_recognition_method: (
        TaxRecognitionMethod | None
    ) = None

    tax_price_mode: TaxPriceMode | None = None

    @field_validator(
        "tax_rate_code",
        mode="before",
    )
    @classmethod
    def normalize_tax_rate_code(
        cls,
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        code = value.strip().upper()

        if not code:
            raise ValueError(
                "Tax rate code cannot be blank"
            )

        return code

    @model_validator(
        mode="after",
    )
    def validate_tax_configuration(
        self,
    ):
        has_rate = (
            self.tax_rate_code is not None
        )
        has_method = (
            self.tax_recognition_method
            is not None
        )
        has_price_mode = (
            self.tax_price_mode is not None
        )

        if not (
            has_rate
            == has_method
            == has_price_mode
        ):
            raise ValueError(
                "tax_rate_code, "
                "tax_recognition_method and "
                "tax_price_mode must be "
                "provided together"
            )

        return self


class TradeDocumentCreate(BaseModel):
    number: str = Field(
        min_length=1,
        max_length=100,
    )

    direction: TradeDirection
    kind: TradeDocumentKind

    document_date: date

    counterparty_id: int = Field(
        gt=0,
    )

    contract_id: int | None = Field(
        default=None,
        gt=0,
    )

    currency_code: str = Field(
        default="UAH",
        min_length=3,
        max_length=3,
    )

    payment_term_days: int = Field(
        default=0,
        ge=0,
    )

    lines: list[
        TradeDocumentLineCreate
    ] = Field(
        min_length=1,
    )

    @field_validator(
        "number",
        mode="before",
    )
    @classmethod
    def normalize_number(
        cls,
        value: Any,
    ) -> Any:
        if not isinstance(value, str):
            return value

        value = value.strip()

        if not value:
            raise ValueError(
                "Document number cannot be blank"
            )

        return value

    @field_validator(
        "currency_code",
        mode="before",
    )
    @classmethod
    def normalize_currency(
        cls,
        value: Any,
    ) -> Any:
        return _normalize_currency_code(
            value
        )


class TradeDocumentUpdate(BaseModel):
    number: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    direction: TradeDirection | None = None
    kind: TradeDocumentKind | None = None

    document_date: date | None = None

    counterparty_id: int | None = Field(
        default=None,
        gt=0,
    )

    contract_id: int | None = Field(
        default=None,
        gt=0,
    )

    currency_code: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
    )

    payment_term_days: int | None = Field(
        default=None,
        ge=0,
    )

    lines: list[
        TradeDocumentLineCreate
    ] | None = Field(
        default=None,
        min_length=1,
    )

    @field_validator(
        "number",
        mode="before",
    )
    @classmethod
    def normalize_number(
        cls,
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        if not value:
            raise ValueError(
                "Document number cannot be blank"
            )

        return value

    @field_validator(
        "currency_code",
        mode="before",
    )
    @classmethod
    def normalize_currency(
        cls,
        value: Any,
    ) -> Any:
        if value is None:
            return None

        return _normalize_currency_code(
            value
        )


class TradeDocumentLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    trade_document_id: int

    line_number: int

    product_id: int
    warehouse_id: int | None

    quantity: Decimal
    unit_price: Decimal

    tax_rate_code: str | None
    tax_recognition_method: (
        TaxRecognitionMethod | None
    )
    tax_price_mode: TaxPriceMode | None


class TradeDocumentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int

    counterparty_id: int
    contract_id: int | None

    number: str

    direction: TradeDirection
    kind: TradeDocumentKind
    status: TradeDocumentStatus

    document_date: date

    currency_code: str
    payment_term_days: int

    created_by: int

    created_at: datetime
    updated_at: datetime

    confirmed_at: datetime | None
    cancelled_at: datetime | None

    lines: list[
        TradeDocumentLineResponse
    ]


class SalesOrderFulfillmentLineRequest(BaseModel):
    """
    One requested Trade Order fulfillment line.

    Product and warehouse are deliberately not accepted here.
    They are derived from the persistent TradeDocumentLine.

    Compatibility class name is retained for existing API clients.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    trade_document_line_id: int = Field(
        gt=0,
    )

    quantity: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
    )


class SalesOrderFulfillmentRequest(BaseModel):
    """
    Fulfill part or all of a confirmed Trade Order.

    SALE creates a warehouse ISSUE.
    PURCHASE creates a warehouse RECEIPT.

    Compatibility class name is retained for existing API clients.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    warehouse_document_number: str = Field(
        min_length=1,
        max_length=50,
    )

    document_date: date

    accounting_rule_id: int = Field(
        gt=0,
    )

    lines: list[
        SalesOrderFulfillmentLineRequest
    ] = Field(
        min_length=1,
    )

    @field_validator(
        "warehouse_document_number",
        mode="before",
    )
    @classmethod
    def normalize_warehouse_document_number(
        cls,
        value: Any,
    ) -> Any:
        if not isinstance(
            value,
            str,
        ):
            return value

        value = value.strip()

        if not value:
            raise ValueError(
                "Warehouse document number "
                "cannot be blank"
            )

        return value


class SalesOrderFulfillmentResponse(BaseModel):
    """
    Persistent results created by one atomic SALE or PURCHASE
    order fulfillment.
    """

    trade_document: TradeDocumentResponse

    warehouse_document_id: int
    fulfillment_id: int
    journal_entry_id: int



class SalesOrderFulfillmentReversalRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    reversal_date: date


class SalesOrderFulfillmentReversalResponse(BaseModel):
    trade_document: TradeDocumentResponse

    warehouse_document_id: int
    fulfillment_id: int
