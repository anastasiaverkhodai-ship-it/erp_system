from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0")


class InputVatFulfillmentBridgeCalculationError(
    Exception
):
    """Base INPUT VAT fulfillment bridge calculation error."""


class InputVatFulfillmentBridgeDataIntegrityError(
    InputVatFulfillmentBridgeCalculationError
):
    """Bridge calculation input is internally inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputVatFulfillmentBridgeCandidate:
    """
    One ACTIVE purchase InvoiceFulfillmentAllocation.

    quantity:
        immutable allocation quantity.

    event_date:
        economic date of the POSTED warehouse RECEIPT.

    source_id:
        InvoiceFulfillmentAllocation.id.
    """

    source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class InputVatFulfillmentBridgeTarget:
    """
    Desired economic INPUT VAT bridge state for one fulfillment source.

    Positive amount later represents:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

        GENERAL 291:
        Dr 644
        Cr 631

    Zero is a valid desired reconciliation state but must never be
    persisted as an original bridge event.
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
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "currency_code must contain "
                "exactly 3 characters"
            )
        )


def _validate_candidate(
    candidate,
) -> InputVatFulfillmentBridgeCandidate:
    if not isinstance(
        candidate,
        InputVatFulfillmentBridgeCandidate,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "candidate must be "
                "InputVatFulfillmentBridgeCandidate"
            )
        )

    if candidate.source_id <= 0:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "source_id must be greater than zero"
            )
        )

    if not isinstance(
        candidate.event_date,
        date,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "event_date must be a date"
            )
        )

    quantity = _decimal(
        candidate.quantity
    )

    if quantity <= ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "candidate quantity must be "
                "greater than zero"
            )
        )

    return InputVatFulfillmentBridgeCandidate(
        source_id=candidate.source_id,
        event_date=candidate.event_date,
        quantity=quantity,
    )


def _cumulative_tax_amount(
    *,
    total_tax_amount: Decimal,
    cumulative_quantity: Decimal,
    invoice_line_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    """
    Cumulative-delta monetary rounding.

    For a full allocation quantity, return the exact rounded immutable
    TaxCalculation tax amount.

    Otherwise:

        round(
            total VAT
            * cumulative quantity
            / invoice quantity
        )

    Individual source amounts are later calculated as:

        cumulative_after - cumulative_before

    This prevents per-allocation rounding from making aggregate bridge
    VAT exceed the immutable TaxCalculation amount.
    """

    if cumulative_quantity == ZERO:
        return round_currency_amount(
            amount=ZERO,
            currency_code=currency_code,
        )

    if (
        cumulative_quantity
        == invoice_line_quantity
    ):
        return round_currency_amount(
            amount=total_tax_amount,
            currency_code=currency_code,
        )

    return round_currency_amount(
        amount=(
            total_tax_amount
            * cumulative_quantity
            / invoice_line_quantity
        ),
        currency_code=currency_code,
    )


def build_input_vat_fulfillment_bridge_targets(
    *,
    tax_calculation_id: int,
    invoice_line_quantity: Decimal,
    tax_amount: Decimal,
    currency_code: str,
    candidates: Iterable[
        InputVatFulfillmentBridgeCandidate
    ],
) -> tuple[
    InputVatFulfillmentBridgeTarget,
    ...,
]:
    """
    Build complete desired economic INPUT VAT bridge targets from all
    ACTIVE purchase fulfillment allocations for one TaxCalculation.

    Ordering:
        event_date
        -> InvoiceFulfillmentAllocation.id

    Monetary truth:
        immutable TaxCalculation.tax_amount.

    Quantity truth:
        immutable TradeDocumentLine.quantity
        + ACTIVE InvoiceFulfillmentAllocation.quantity.

    Cumulative-delta rounding guarantees:

        sum(active bridge targets)
        <= TaxCalculation.tax_amount

    and, when the full invoice-line quantity is fulfilled:

        sum(active bridge targets)
        == TaxCalculation.tax_amount

    A source may receive amount == 0 when currency rounding assigns the
    residual to another source. Zero targets are intentionally retained
    for reconciliation planning but must not be persisted as originals.
    """

    if (
        not isinstance(
            tax_calculation_id,
            int,
        )
        or tax_calculation_id <= 0
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "tax_calculation_id must be "
                "greater than zero"
            )
        )

    _validate_currency_code(
        currency_code
    )

    invoice_quantity = _decimal(
        invoice_line_quantity
    )

    if invoice_quantity <= ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "invoice_line_quantity must be "
                "greater than zero"
            )
        )

    raw_tax_amount = _decimal(
        tax_amount
    )

    if raw_tax_amount < ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "tax_amount cannot be negative"
            )
        )

    total_tax = round_currency_amount(
        amount=raw_tax_amount,
        currency_code=currency_code,
    )

    validated = []
    source_ids = set()

    for raw_candidate in tuple(
        candidates
    ):
        candidate = _validate_candidate(
            raw_candidate
        )

        if candidate.source_id in source_ids:
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "Duplicate INPUT VAT fulfillment "
                    "bridge source_id"
                )
            )

        source_ids.add(
            candidate.source_id
        )

        validated.append(
            candidate
        )

    ordered = tuple(
        sorted(
            validated,
            key=lambda item: (
                item.event_date,
                item.source_id,
            ),
        )
    )

    cumulative_quantity = ZERO

    cumulative_tax_before = (
        _cumulative_tax_amount(
            total_tax_amount=total_tax,
            cumulative_quantity=ZERO,
            invoice_line_quantity=(
                invoice_quantity
            ),
            currency_code=currency_code,
        )
    )

    targets = []

    for candidate in ordered:
        cumulative_after = (
            cumulative_quantity
            + candidate.quantity
        )

        if (
            cumulative_after
            > invoice_quantity
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "ACTIVE fulfillment allocation "
                    "quantity exceeds invoice line "
                    "quantity"
                )
            )

        cumulative_tax_after = (
            _cumulative_tax_amount(
                total_tax_amount=total_tax,
                cumulative_quantity=(
                    cumulative_after
                ),
                invoice_line_quantity=(
                    invoice_quantity
                ),
                currency_code=currency_code,
            )
        )

        source_amount = (
            cumulative_tax_after
            - cumulative_tax_before
        )

        if source_amount < ZERO:
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "Calculated INPUT VAT fulfillment "
                    "bridge amount cannot be negative"
                )
            )

        targets.append(
            InputVatFulfillmentBridgeTarget(
                tax_calculation_id=(
                    tax_calculation_id
                ),
                source_id=(
                    candidate.source_id
                ),
                event_date=(
                    candidate.event_date
                ),
                amount=source_amount,
                currency_code=currency_code,
            )
        )

        cumulative_quantity = (
            cumulative_after
        )

        cumulative_tax_before = (
            cumulative_tax_after
        )

    expected_total = (
        _cumulative_tax_amount(
            total_tax_amount=total_tax,
            cumulative_quantity=(
                cumulative_quantity
            ),
            invoice_line_quantity=(
                invoice_quantity
            ),
            currency_code=currency_code,
        )
    )

    actual_total = sum(
        (
            target.amount
            for target in targets
        ),
        ZERO,
    )

    if actual_total != expected_total:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "aggregate rounding invariant failed"
            )
        )

    if (
        cumulative_quantity
        == invoice_quantity
        and actual_total != total_tax
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Fully fulfilled INPUT VAT bridge "
                "must equal TaxCalculation tax amount"
            )
        )

    return tuple(
        targets
    )
