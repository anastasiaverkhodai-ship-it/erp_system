from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0")


class SalesRecognitionCalculationError(Exception):
    """Base Sales recognition calculation error."""


class SalesRecognitionQuantityError(
    SalesRecognitionCalculationError
):
    """Recognition quantity state is invalid."""


class SalesRecognitionAmountError(
    SalesRecognitionCalculationError
):
    """Invoice monetary snapshot is invalid."""


class SalesRecognitionCandidateError(
    SalesRecognitionCalculationError
):
    """Fulfillment recognition candidate is invalid."""


class DuplicateSalesRecognitionSourceError(
    SalesRecognitionCandidateError
):
    """More than one candidate uses the same source."""


class SalesRecognitionDataIntegrityError(
    SalesRecognitionCalculationError
):
    """Current and desired recognition state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionSlice:
    """
    Monetary slice represented by one fulfillment allocation.

    gross_amount:
        commercial tax-inclusive amount to be recognized through
        Dr CUSTOMER_RECEIVABLES / Cr GOODS_REVENUE.

    tax_amount:
        VAT economically contained in this commercial slice.

        This value does NOT itself choose the VAT GL posting.
        Later lifecycle logic distinguishes:
          - fulfillment-first VAT, and
          - prepaid VAT bridge closure.

    Cumulative-delta rounding guarantees that sequential slices
    reconcile exactly to the immutable invoice-line totals.
    """

    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionCandidate:
    """
    One ACTIVE InvoiceFulfillmentAllocation represented in the
    pure recognition engine.

    event_date is the economic warehouse ISSUE document date.
    source_id is InvoiceFulfillmentAllocation.id.
    """

    source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionTarget:
    """
    Desired net Sales recognition state for one fulfillment source.

    A zero target is used only by reconciliation to mean that an
    already-recognized source must be fully reversed.
    """

    source_id: int
    event_date: date
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.quantity == ZERO
            and self.gross_amount == ZERO
            and self.tax_amount == ZERO
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
    )


def _validate_invoice_quantity(
    invoice_line_quantity: Decimal,
) -> Decimal:
    quantity = _decimal(
        invoice_line_quantity
    )

    if quantity <= ZERO:
        raise SalesRecognitionQuantityError(
            "Invoice line quantity must be greater than zero"
        )

    return quantity


def _validate_quantity_state(
    *,
    invoice_line_quantity: Decimal,
    allocated_quantity_before: Decimal,
    allocation_quantity: Decimal,
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
]:
    invoice_quantity = _validate_invoice_quantity(
        invoice_line_quantity
    )

    allocated_before = _decimal(
        allocated_quantity_before
    )
    allocated_now = _decimal(
        allocation_quantity
    )

    if allocated_before < ZERO:
        raise SalesRecognitionQuantityError(
            "Allocated quantity before cannot be negative"
        )

    if allocated_now <= ZERO:
        raise SalesRecognitionQuantityError(
            "Allocation quantity must be greater than zero"
        )

    allocated_after = (
        allocated_before
        + allocated_now
    )

    if allocated_before > invoice_quantity:
        raise SalesRecognitionQuantityError(
            "Allocated quantity before cannot exceed "
            "invoice line quantity"
        )

    if allocated_after > invoice_quantity:
        raise SalesRecognitionQuantityError(
            "Sales recognition allocation exceeds "
            "invoice line quantity"
        )

    return (
        invoice_quantity,
        allocated_before,
        allocated_after,
    )


def _validate_amounts(
    *,
    invoice_line_gross_amount: Decimal,
    invoice_line_tax_amount: Decimal,
) -> tuple[
    Decimal,
    Decimal,
]:
    gross = _decimal(
        invoice_line_gross_amount
    )
    tax = _decimal(
        invoice_line_tax_amount
    )

    if gross <= ZERO:
        raise SalesRecognitionAmountError(
            "Invoice line gross amount must be "
            "greater than zero"
        )

    if tax < ZERO:
        raise SalesRecognitionAmountError(
            "Invoice line tax amount cannot be negative"
        )

    if tax > gross:
        raise SalesRecognitionAmountError(
            "Invoice line tax amount cannot exceed "
            "gross amount"
        )

    return (
        gross,
        tax,
    )


def _cumulative_amount(
    *,
    total_amount: Decimal,
    cumulative_quantity: Decimal,
    invoice_line_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity == ZERO:
        return round_currency_amount(
            amount=ZERO,
            currency_code=currency_code,
        )

    if cumulative_quantity == invoice_line_quantity:
        return round_currency_amount(
            amount=total_amount,
            currency_code=currency_code,
        )

    return round_currency_amount(
        amount=(
            total_amount
            * cumulative_quantity
            / invoice_line_quantity
        ),
        currency_code=currency_code,
    )


def calculate_sales_recognition_slice(
    *,
    invoice_line_quantity: Decimal,
    allocated_quantity_before: Decimal,
    allocation_quantity: Decimal,
    invoice_line_gross_amount: Decimal,
    invoice_line_tax_amount: Decimal,
    currency_code: str,
) -> SalesRecognitionSlice:
    """
    Calculate the commercial monetary slice represented by one
    sequential InvoiceFulfillmentAllocation.

    Monetary truth comes from the immutable confirmed Invoice /
    TaxCalculation snapshot, never from warehouse ISSUE price.

    Cumulative-delta rounding:

        rounded(total * after_ratio)
        -
        rounded(total * before_ratio)

    guarantees exact reconciliation to the invoice-line total.
    """

    (
        invoice_quantity,
        allocated_before,
        allocated_after,
    ) = _validate_quantity_state(
        invoice_line_quantity=(
            invoice_line_quantity
        ),
        allocated_quantity_before=(
            allocated_quantity_before
        ),
        allocation_quantity=(
            allocation_quantity
        ),
    )

    (
        gross_total,
        tax_total,
    ) = _validate_amounts(
        invoice_line_gross_amount=(
            invoice_line_gross_amount
        ),
        invoice_line_tax_amount=(
            invoice_line_tax_amount
        ),
    )

    gross_before = _cumulative_amount(
        total_amount=gross_total,
        cumulative_quantity=allocated_before,
        invoice_line_quantity=invoice_quantity,
        currency_code=currency_code,
    )

    gross_after = _cumulative_amount(
        total_amount=gross_total,
        cumulative_quantity=allocated_after,
        invoice_line_quantity=invoice_quantity,
        currency_code=currency_code,
    )

    tax_before = _cumulative_amount(
        total_amount=tax_total,
        cumulative_quantity=allocated_before,
        invoice_line_quantity=invoice_quantity,
        currency_code=currency_code,
    )

    tax_after = _cumulative_amount(
        total_amount=tax_total,
        cumulative_quantity=allocated_after,
        invoice_line_quantity=invoice_quantity,
        currency_code=currency_code,
    )

    recognized_gross = (
        gross_after
        - gross_before
    )

    recognized_tax = (
        tax_after
        - tax_before
    )

    if recognized_gross <= ZERO:
        raise SalesRecognitionAmountError(
            "Calculated Sales recognition gross amount "
            "must be greater than zero"
        )

    if recognized_tax < ZERO:
        raise SalesRecognitionAmountError(
            "Calculated Sales recognition tax amount "
            "cannot be negative"
        )

    if recognized_tax > recognized_gross:
        raise SalesRecognitionAmountError(
            "Calculated Sales recognition tax amount "
            "cannot exceed gross amount"
        )

    return SalesRecognitionSlice(
        quantity=(
            allocated_after
            - allocated_before
        ),
        gross_amount=recognized_gross,
        tax_amount=recognized_tax,
    )


def _candidate_sort_key(
    candidate: SalesRecognitionCandidate,
) -> tuple[
    date,
    int,
]:
    return (
        candidate.event_date,
        candidate.source_id,
    )


def _target_sort_key(
    target: SalesRecognitionTarget,
) -> tuple[
    date,
    int,
]:
    return (
        target.event_date,
        target.source_id,
    )


def _validate_candidates(
    candidates: Iterable[
        SalesRecognitionCandidate
    ],
) -> tuple[
    SalesRecognitionCandidate,
    ...,
]:
    result = tuple(
        candidates
    )

    seen_source_ids: set[int] = set()

    for candidate in result:
        if not isinstance(
            candidate,
            SalesRecognitionCandidate,
        ):
            raise SalesRecognitionCandidateError(
                "Sales recognition candidate must be "
                "SalesRecognitionCandidate"
            )

        if candidate.source_id <= 0:
            raise SalesRecognitionCandidateError(
                "Sales recognition source_id must be "
                "greater than zero"
            )

        if not isinstance(
            candidate.event_date,
            date,
        ):
            raise SalesRecognitionCandidateError(
                "Sales recognition event_date must be a date"
            )

        quantity = _decimal(
            candidate.quantity
        )

        if quantity <= ZERO:
            raise SalesRecognitionCandidateError(
                "Sales recognition candidate quantity "
                "must be greater than zero"
            )

        if candidate.source_id in seen_source_ids:
            raise DuplicateSalesRecognitionSourceError(
                "Duplicate Sales recognition source"
            )

        seen_source_ids.add(
            candidate.source_id
        )

    return tuple(
        sorted(
            result,
            key=_candidate_sort_key,
        )
    )


def build_sales_recognition_targets(
    *,
    invoice_line_quantity: Decimal,
    invoice_line_gross_amount: Decimal,
    invoice_line_tax_amount: Decimal,
    currency_code: str,
    candidates: Iterable[
        SalesRecognitionCandidate
    ],
) -> tuple[
    SalesRecognitionTarget,
    ...,
]:
    """
    Build desired commercial recognition targets for all ACTIVE
    fulfillment allocations of one immutable Invoice line.

    Ordering is deterministic and follows economic fulfillment:

        warehouse ISSUE date
        InvoiceFulfillmentAllocation.id

    Therefore reversal/reallocation can rebuild rounding
    deterministically from the remaining ACTIVE sources.
    """

    invoice_quantity = _validate_invoice_quantity(
        invoice_line_quantity
    )

    (
        gross_total,
        tax_total,
    ) = _validate_amounts(
        invoice_line_gross_amount=(
            invoice_line_gross_amount
        ),
        invoice_line_tax_amount=(
            invoice_line_tax_amount
        ),
    )

    ordered_candidates = _validate_candidates(
        candidates
    )

    allocated_before = ZERO
    targets = []

    for candidate in ordered_candidates:
        candidate_quantity = _decimal(
            candidate.quantity
        )

        recognition_slice = (
            calculate_sales_recognition_slice(
                invoice_line_quantity=(
                    invoice_quantity
                ),
                allocated_quantity_before=(
                    allocated_before
                ),
                allocation_quantity=(
                    candidate_quantity
                ),
                invoice_line_gross_amount=(
                    gross_total
                ),
                invoice_line_tax_amount=(
                    tax_total
                ),
                currency_code=currency_code,
            )
        )

        targets.append(
            SalesRecognitionTarget(
                source_id=candidate.source_id,
                event_date=candidate.event_date,
                quantity=recognition_slice.quantity,
                gross_amount=(
                    recognition_slice.gross_amount
                ),
                tax_amount=(
                    recognition_slice.tax_amount
                ),
            )
        )

        allocated_before += (
            candidate_quantity
        )

    return tuple(
        targets
    )


def _target_map(
    targets: Iterable[
        SalesRecognitionTarget
    ],
) -> dict[
    int,
    SalesRecognitionTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            SalesRecognitionTarget,
        ):
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target must be "
                "SalesRecognitionTarget"
            )

        if target.source_id <= 0:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target source_id "
                "must be greater than zero"
            )

        if target.source_id in result:
            raise SalesRecognitionDataIntegrityError(
                "Duplicate Sales recognition target source"
            )

        if not isinstance(
            target.event_date,
            date,
        ):
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target event_date "
                "must be a date"
            )

        quantity = _decimal(
            target.quantity
        )
        gross = _decimal(
            target.gross_amount
        )
        tax = _decimal(
            target.tax_amount
        )

        if quantity < ZERO:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target quantity "
                "cannot be negative"
            )

        if gross < ZERO:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target gross amount "
                "cannot be negative"
            )

        if tax < ZERO:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target tax amount "
                "cannot be negative"
            )

        if tax > gross:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition target tax amount "
                "cannot exceed gross amount"
            )

        if (
            quantity == ZERO
            or gross == ZERO
        ) and not (
            quantity == ZERO
            and gross == ZERO
            and tax == ZERO
        ):
            raise SalesRecognitionDataIntegrityError(
                "Zero Sales recognition target must have "
                "zero quantity, gross and tax"
            )

        result[
            target.source_id
        ] = target

    return result


def order_sales_recognition_reconciliations(
    *,
    current_targets: Iterable[
        SalesRecognitionTarget
    ],
    desired_targets: Iterable[
        SalesRecognitionTarget
    ],
) -> tuple[
    SalesRecognitionTarget,
    ...,
]:
    """
    Produce safe reconciliation order.

    A returned target means:
        reconcile this source to exactly this desired net state.

    Sources that shrink or disappear are returned before sources
    that grow. This prevents temporary over-recognition while
    rounding pennies are reassigned after allocation reversal.

    If the same source exists in current and desired state, its
    economic identity (date and quantity) must remain unchanged.
    """

    current = _target_map(
        current_targets
    )
    desired = _target_map(
        desired_targets
    )

    decreases = []
    increases = []

    all_source_ids = (
        set(current)
        | set(desired)
    )

    for source_id in all_source_ids:
        current_target = current.get(
            source_id
        )
        desired_target = desired.get(
            source_id
        )

        if (
            current_target is not None
            and desired_target is not None
        ):
            if (
                current_target.event_date
                != desired_target.event_date
            ):
                raise SalesRecognitionDataIntegrityError(
                    "Sales recognition source event_date "
                    "changed unexpectedly"
                )

            if (
                _decimal(
                    current_target.quantity
                )
                != _decimal(
                    desired_target.quantity
                )
            ):
                raise SalesRecognitionDataIntegrityError(
                    "Sales recognition source quantity "
                    "changed unexpectedly"
                )

            if (
                _decimal(
                    current_target.gross_amount
                )
                == _decimal(
                    desired_target.gross_amount
                )
                and _decimal(
                    current_target.tax_amount
                )
                == _decimal(
                    desired_target.tax_amount
                )
            ):
                continue

            # Any reduction is processed before increases.
            if (
                _decimal(
                    desired_target.gross_amount
                )
                < _decimal(
                    current_target.gross_amount
                )
                or _decimal(
                    desired_target.tax_amount
                )
                < _decimal(
                    current_target.tax_amount
                )
            ):
                decreases.append(
                    desired_target
                )
            else:
                increases.append(
                    desired_target
                )

            continue

        if current_target is not None:
            decreases.append(
                SalesRecognitionTarget(
                    source_id=source_id,
                    event_date=(
                        current_target.event_date
                    ),
                    quantity=ZERO,
                    gross_amount=ZERO,
                    tax_amount=ZERO,
                )
            )
            continue

        if desired_target is not None:
            increases.append(
                desired_target
            )

    return tuple(
        sorted(
            decreases,
            key=_target_sort_key,
        )
        + sorted(
            increases,
            key=_target_sort_key,
        )
    )
