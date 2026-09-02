from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0")


class VatAdvanceBridgeCalculationError(Exception):
    """Base VAT advance bridge calculation error."""


class VatAdvanceBridgeDataIntegrityError(
    VatAdvanceBridgeCalculationError
):
    """Bridge source data is internally inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class VatAdvanceBridgeTarget:
    """
    Complete desired bridge state for one fulfillment source.

    source_id is InvoiceFulfillmentAllocation.id.

    amount is the VAT economically contained in recognized Sales
    revenue that has NOT already been removed from revenue through
    fulfillment-source OUTPUT VAT accounting.

    Positive amount later produces:

        Dr GOODS_REVENUE
        Cr VAT_OUTPUT

        Dr 702
        Cr 643

    Zero amount means no active bridge event should exist.
    """

    tax_calculation_id: int
    source_id: int
    event_date: date
    amount: Decimal
    currency_code: str

    @property
    def is_zero(
        self,
    ) -> bool:
        return self.amount == ZERO


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
    )


def _validate_currency_code(
    currency_code: str,
) -> None:
    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(currency_code) != 3
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge currency_code "
            "must contain exactly 3 characters"
        )


def calculate_vat_advance_bridge_amount(
    *,
    sales_tax_amount: Decimal,
    fulfillment_tax_amount: Decimal,
    currency_code: str,
) -> Decimal:
    """
    Calculate the VAT amount that must remain bridged through account
    643 after commercial Sales recognition.

    Formula:

        bridge VAT
            =
        SalesRecognition VAT
            -
        VAT already recognized through this fulfillment source

    Examples:

        settlement/prepayment first:
            20 - 0 = 20

        fulfillment first:
            20 - 20 = 0

        partial prepayment first:
            20 - 10 = 10

        cash method at fulfillment:
            20 - 0 = 20
    """

    _validate_currency_code(
        currency_code
    )

    sales_tax = round_currency_amount(
        amount=_decimal(
            sales_tax_amount
        ),
        currency_code=currency_code,
    )

    fulfillment_tax = round_currency_amount(
        amount=_decimal(
            fulfillment_tax_amount
        ),
        currency_code=currency_code,
    )

    if sales_tax < ZERO:
        raise VatAdvanceBridgeDataIntegrityError(
            "Sales recognition VAT amount "
            "cannot be negative"
        )

    if fulfillment_tax < ZERO:
        raise VatAdvanceBridgeDataIntegrityError(
            "Fulfillment-source VAT amount "
            "cannot be negative"
        )

    if fulfillment_tax > sales_tax:
        raise VatAdvanceBridgeDataIntegrityError(
            "Fulfillment-source VAT amount "
            "cannot exceed Sales recognition VAT amount"
        )

    return round_currency_amount(
        amount=(
            sales_tax
            - fulfillment_tax
        ),
        currency_code=currency_code,
    )


def build_vat_advance_bridge_target(
    *,
    tax_calculation_id: int,
    source_id: int,
    event_date: date,
    sales_tax_amount: Decimal,
    fulfillment_tax_amount: Decimal,
    currency_code: str,
) -> VatAdvanceBridgeTarget:
    """
    Build one complete desired VAT advance bridge target.

    event_date is the commercial Sales-recognition date for the
    fulfillment source.
    """

    if (
        not isinstance(
            tax_calculation_id,
            int,
        )
        or tax_calculation_id <= 0
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge tax_calculation_id "
            "must be greater than zero"
        )

    if (
        not isinstance(
            source_id,
            int,
        )
        or source_id <= 0
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge source_id "
            "must be greater than zero"
        )

    if not isinstance(
        event_date,
        date,
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge event_date "
            "must be a date"
        )

    amount = (
        calculate_vat_advance_bridge_amount(
            sales_tax_amount=(
                sales_tax_amount
            ),
            fulfillment_tax_amount=(
                fulfillment_tax_amount
            ),
            currency_code=currency_code,
        )
    )

    return VatAdvanceBridgeTarget(
        tax_calculation_id=(
            tax_calculation_id
        ),
        source_id=source_id,
        event_date=event_date,
        amount=amount,
        currency_code=currency_code,
    )
