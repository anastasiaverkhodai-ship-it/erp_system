from collections.abc import Iterable
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
)
from decimal import (
    Decimal,
    InvalidOperation,
)

from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.trade_return_calculation_service import (
    TradeValueCorrectionError,
    calculate_trade_value_correction,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionAllocationCalculationError(
    Exception
):
    """Base Purchase Value Correction allocation error."""


class PurchaseValueCorrectionAllocationDataIntegrityError(
    PurchaseValueCorrectionAllocationCalculationError
):
    """Source identity, chronology, or monetary truth is invalid."""


class PurchaseValueCorrectionAllocationCapacityError(
    PurchaseValueCorrectionAllocationCalculationError
):
    """ACTIVE fulfillment quantity exceeds invoice-line capacity."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionAllocationCandidate:
    """
    One ACTIVE InvoiceFulfillmentAllocation available for
    Purchase Value Correction allocation.

    invoice_fulfillment_allocation_id:
        immutable InvoiceFulfillmentAllocation.id.

    receipt_event_date:
        economic date of the POSTED warehouse receipt,
        normally Document.document_date.

    quantity:
        immutable ACTIVE allocation quantity.

    Ordering for cumulative monetary allocation is always:

        receipt_event_date
        ->
        InvoiceFulfillmentAllocation.id

    It deliberately does not use correction recognition_date,
    because multiple historical receipts can share one later
    correction date while retaining distinct receipt chronology.
    """

    invoice_fulfillment_allocation_id: int
    receipt_event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionAllocationTarget:
    """
    Pure desired economic allocation for one:

        TradeValueCorrectionEvent
        x
        InvoiceFulfillmentAllocation

    original_allocated_base_amount and
    corrected_allocated_base_amount are VAT-exclusive.

    recognition_date:

        max(
            correction_date,
            receipt_event_date,
        )

    A target may be a monetary no-op because cumulative currency
    rounding can assign the entire correction residual to another
    source.

    Such no-op targets are intentionally retained by calculation.
    Persistence must not create an original immutable event for a
    no-op target, but later reconciliation can use the target to
    reverse stale historical state.
    """

    trade_value_correction_event_id: int
    invoice_fulfillment_allocation_id: int
    recognition_date: date
    original_allocated_base_amount: Decimal
    corrected_allocated_base_amount: Decimal
    currency_code: str

    @property
    def allocated_base_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_allocated_base_amount
            - self.original_allocated_base_amount
        )

    @property
    def is_noop(
        self,
    ) -> bool:
        return (
            self.allocated_base_delta
            == ZERO
        )


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if (
        not isinstance(
            value,
            date,
        )
        or isinstance(
            value,
            datetime,
        )
    ):
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _decimal(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    if isinstance(
        value,
        bool,
    ):
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} must be numeric"
            )
        )

    try:
        result = Decimal(
            value
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} must be numeric"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _money(
    value: Decimal,
    *,
    currency_code: str,
    field: str,
) -> Decimal:
    try:
        return round_currency_amount(
            amount=value,
            currency_code=currency_code,
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                f"{field} cannot be currency-rounded"
            )
        ) from exc


def _cumulative_base(
    *,
    total_base_amount: Decimal,
    cumulative_quantity: Decimal,
    invoice_line_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity == ZERO:
        return _money(
            ZERO,
            currency_code=currency_code,
            field="Cumulative base",
        )

    if (
        cumulative_quantity
        == invoice_line_quantity
    ):
        return _money(
            total_base_amount,
            currency_code=currency_code,
            field="Full cumulative base",
        )

    return _money(
        (
            total_base_amount
            * cumulative_quantity
            / invoice_line_quantity
        ),
        currency_code=currency_code,
        field="Cumulative base",
    )


def build_purchase_value_correction_allocation_targets(
    *,
    trade_value_correction_event_id: int,
    correction_date: date,
    invoice_line_quantity: Decimal,
    original_gross_amount: Decimal,
    original_tax_amount: Decimal,
    corrected_gross_amount: Decimal,
    corrected_tax_amount: Decimal,
    currency_code: str,
    candidates: Iterable[
        PurchaseValueCorrectionAllocationCandidate
    ],
) -> tuple[
    PurchaseValueCorrectionAllocationTarget,
    ...,
]:
    """
    Build complete desired Purchase Value Correction allocations
    for the currently ACTIVE fulfillment sources supplied by the
    caller.

    Commercial truth:
        calculate_trade_value_correction()

    Quantity truth:
        immutable invoice-line quantity
        +
        ACTIVE InvoiceFulfillmentAllocation quantities.

    Source chronology:
        receipt_event_date
        ->
        InvoiceFulfillmentAllocation.id.

    Recognition chronology:
        max(
            correction_date,
            receipt_event_date,
        )

    Monetary allocation:
        cumulative-delta rounding is calculated independently for
        the original VAT-exclusive base and corrected VAT-exclusive
        base.

    Therefore:
        sum(source original bases)
        ==
        cumulative original base represented by fulfilled quantity

    and:
        sum(source corrected bases)
        ==
        cumulative corrected base represented by fulfilled quantity.

    When the full invoice-line quantity is fulfilled, both sums
    equal their exact currency-rounded full-line bases.

    This function is pure. It performs no DB writes, inventory
    mutation, accounting, supplier clearing, or legal VAT actions.
    """

    correction_event_id = _positive_id(
        trade_value_correction_event_id,
        field=(
            "trade_value_correction_event_id"
        ),
    )

    normalized_correction_date = _business_date(
        correction_date,
        field="correction_date",
    )

    invoice_quantity = _decimal(
        invoice_line_quantity,
        field="invoice_line_quantity",
    )

    if invoice_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionAllocationCapacityError(
                "invoice_line_quantity must be positive"
            )
        )

    try:
        correction = (
            calculate_trade_value_correction(
                original_gross_amount=(
                    original_gross_amount
                ),
                original_tax_amount=(
                    original_tax_amount
                ),
                corrected_gross_amount=(
                    corrected_gross_amount
                ),
                corrected_tax_amount=(
                    corrected_tax_amount
                ),
                currency_code=currency_code,
            )
        )
    except TradeValueCorrectionError as exc:
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                "Trade Value Correction commercial state "
                "is invalid"
            )
        ) from exc

    currency = correction.currency_code

    original_base_total = _money(
        (
            correction.original_gross_amount
            - correction.original_tax_amount
        ),
        currency_code=currency,
        field="Original full-line base",
    )

    corrected_base_total = _money(
        (
            correction.corrected_gross_amount
            - correction.corrected_tax_amount
        ),
        currency_code=currency,
        field="Corrected full-line base",
    )

    validated = []
    source_ids = set()

    for candidate in tuple(
        candidates
    ):
        if not isinstance(
            candidate,
            PurchaseValueCorrectionAllocationCandidate,
        ):
            raise (
                PurchaseValueCorrectionAllocationDataIntegrityError(
                    "candidate must be "
                    "PurchaseValueCorrectionAllocationCandidate"
                )
            )

        source_id = _positive_id(
            candidate.invoice_fulfillment_allocation_id,
            field=(
                "invoice_fulfillment_allocation_id"
            ),
        )

        if source_id in source_ids:
            raise (
                PurchaseValueCorrectionAllocationDataIntegrityError(
                    "InvoiceFulfillmentAllocation IDs "
                    "must be unique"
                )
            )

        source_ids.add(
            source_id
        )

        receipt_event_date = _business_date(
            candidate.receipt_event_date,
            field="receipt_event_date",
        )

        quantity = _decimal(
            candidate.quantity,
            field="candidate quantity",
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionAllocationCapacityError(
                    "candidate quantity must be positive"
                )
            )

        validated.append(
            (
                receipt_event_date,
                source_id,
                quantity,
            )
        )

    ordered = tuple(
        sorted(
            validated,
            key=lambda item: (
                item[0],
                item[1],
            ),
        )
    )

    allocated_quantity = sum(
        (
            item[2]
            for item in ordered
        ),
        ZERO,
    )

    if (
        allocated_quantity
        > invoice_quantity
    ):
        raise (
            PurchaseValueCorrectionAllocationCapacityError(
                "ACTIVE allocation quantity exceeds "
                "invoice-line quantity"
            )
        )

    cumulative_quantity = ZERO

    original_cumulative_before = _money(
        ZERO,
        currency_code=currency,
        field="Original cumulative base",
    )

    corrected_cumulative_before = _money(
        ZERO,
        currency_code=currency,
        field="Corrected cumulative base",
    )

    targets = []

    for (
        receipt_event_date,
        source_id,
        quantity,
    ) in ordered:
        cumulative_after = (
            cumulative_quantity
            + quantity
        )

        original_cumulative_after = (
            _cumulative_base(
                total_base_amount=(
                    original_base_total
                ),
                cumulative_quantity=(
                    cumulative_after
                ),
                invoice_line_quantity=(
                    invoice_quantity
                ),
                currency_code=currency,
            )
        )

        corrected_cumulative_after = (
            _cumulative_base(
                total_base_amount=(
                    corrected_base_total
                ),
                cumulative_quantity=(
                    cumulative_after
                ),
                invoice_line_quantity=(
                    invoice_quantity
                ),
                currency_code=currency,
            )
        )

        original_source_base = (
            original_cumulative_after
            - original_cumulative_before
        )

        corrected_source_base = (
            corrected_cumulative_after
            - corrected_cumulative_before
        )

        if (
            original_source_base < ZERO
            or corrected_source_base < ZERO
        ):
            raise (
                PurchaseValueCorrectionAllocationDataIntegrityError(
                    "Cumulative allocation produced "
                    "a negative source base"
                )
            )

        recognition_date = max(
            normalized_correction_date,
            receipt_event_date,
        )

        targets.append(
            PurchaseValueCorrectionAllocationTarget(
                trade_value_correction_event_id=(
                    correction_event_id
                ),
                invoice_fulfillment_allocation_id=(
                    source_id
                ),
                recognition_date=(
                    recognition_date
                ),
                original_allocated_base_amount=(
                    original_source_base
                ),
                corrected_allocated_base_amount=(
                    corrected_source_base
                ),
                currency_code=currency,
            )
        )

        cumulative_quantity = (
            cumulative_after
        )

        original_cumulative_before = (
            original_cumulative_after
        )

        corrected_cumulative_before = (
            corrected_cumulative_after
        )

    expected_original = _cumulative_base(
        total_base_amount=(
            original_base_total
        ),
        cumulative_quantity=(
            cumulative_quantity
        ),
        invoice_line_quantity=(
            invoice_quantity
        ),
        currency_code=currency,
    )

    expected_corrected = _cumulative_base(
        total_base_amount=(
            corrected_base_total
        ),
        cumulative_quantity=(
            cumulative_quantity
        ),
        invoice_line_quantity=(
            invoice_quantity
        ),
        currency_code=currency,
    )

    actual_original = sum(
        (
            target.original_allocated_base_amount
            for target in targets
        ),
        ZERO,
    )

    actual_corrected = sum(
        (
            target.corrected_allocated_base_amount
            for target in targets
        ),
        ZERO,
    )

    if actual_original != expected_original:
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                "Original-base cumulative rounding "
                "invariant failed"
            )
        )

    if actual_corrected != expected_corrected:
        raise (
            PurchaseValueCorrectionAllocationDataIntegrityError(
                "Corrected-base cumulative rounding "
                "invariant failed"
            )
        )

    if (
        cumulative_quantity
        == invoice_quantity
    ):
        if (
            actual_original
            != original_base_total
        ):
            raise (
                PurchaseValueCorrectionAllocationDataIntegrityError(
                    "Fully allocated original base "
                    "must equal full-line original base"
                )
            )

        if (
            actual_corrected
            != corrected_base_total
        ):
            raise (
                PurchaseValueCorrectionAllocationDataIntegrityError(
                    "Fully allocated corrected base "
                    "must equal full-line corrected base"
                )
            )

    return tuple(
        targets
    )
