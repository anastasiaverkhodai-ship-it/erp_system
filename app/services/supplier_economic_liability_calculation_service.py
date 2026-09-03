from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.supplier_advance_clearing_calculation_service import (
    SupplierEconomicLiabilityCandidate,
)


ZERO = Decimal("0.00")


class SupplierEconomicLiabilityCalculationError(
    Exception
):
    """Base supplier economic-liability calculation error."""


class SupplierEconomicLiabilitySourceError(
    SupplierEconomicLiabilityCalculationError
):
    """Source identity or chronology is invalid."""


class SupplierEconomicLiabilityAmountError(
    SupplierEconomicLiabilityCalculationError
):
    """Economic-liability amount is invalid."""


class SupplierEconomicLiabilityCurrencyError(
    SupplierEconomicLiabilityCalculationError
):
    """Currency code is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierReceiptBaseAllocationCandidate:
    """
    One ACTIVE InvoiceFulfillmentAllocation consuming quantity
    from one POSTED purchase receipt line.

    source_id
        InvoiceFulfillmentAllocation.id.

    event_date
        Economic date of the POSTED receipt document.

    quantity
        Immutable ACTIVE allocation quantity.
    """

    source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierReceiptBaseAllocationTarget:
    """
    Exact VAT-exclusive receipt-base amount assigned to one
    InvoiceFulfillmentAllocation.
    """

    source_id: int
    event_date: date
    amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierVatLiabilityComponent:
    """
    ACTIVE economic INPUT VAT bridge amount for one allocation.

    source_id
        InvoiceFulfillmentAllocation.id.

    event_date
        Economic receipt date represented by the bridge.

    amount
        Sum or individual positive active bridge capacity supplied
        by the reconciliation layer.
    """

    source_id: int
    event_date: date
    amount: Decimal


def _currency(
    currency_code: str,
) -> str:
    if not isinstance(
        currency_code,
        str,
    ):
        raise SupplierEconomicLiabilityCurrencyError(
            "currency_code must be a string"
        )

    normalized = (
        currency_code
        .strip()
        .upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise SupplierEconomicLiabilityCurrencyError(
            "currency_code must contain "
            "exactly three letters"
        )

    return normalized


def _source_id(
    value: int,
    *,
    label: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise SupplierEconomicLiabilitySourceError(
            f"{label} source_id must be "
            "a positive integer"
        )

    return value


def _event_date(
    value: date,
    *,
    label: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise SupplierEconomicLiabilitySourceError(
            f"{label} event_date must be a date"
        )

    return value


def _decimal(
    value,
    *,
    label: str,
) -> Decimal:
    try:
        return Decimal(
            str(value)
        )
    except Exception as exc:
        raise SupplierEconomicLiabilityAmountError(
            f"{label} value is invalid"
        ) from exc


def _money(
    value,
    *,
    currency_code: str,
    label: str,
) -> Decimal:
    raw = _decimal(
        value,
        label=label,
    )

    try:
        return round_currency_amount(
            amount=raw,
            currency_code=currency_code,
        )
    except Exception as exc:
        raise SupplierEconomicLiabilityAmountError(
            f"{label} amount cannot be rounded"
        ) from exc


def _cumulative_base(
    *,
    receipt_base_amount: Decimal,
    cumulative_quantity: Decimal,
    receipt_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity == ZERO:
        return _money(
            ZERO,
            currency_code=currency_code,
            label="Cumulative base",
        )

    if cumulative_quantity == receipt_quantity:
        return receipt_base_amount

    return _money(
        (
            receipt_base_amount
            * cumulative_quantity
            / receipt_quantity
        ),
        currency_code=currency_code,
        label="Cumulative base",
    )


def build_supplier_receipt_base_allocation_targets(
    *,
    receipt_quantity: Decimal,
    receipt_base_amount: Decimal,
    currency_code: str,
    candidates: tuple[
        SupplierReceiptBaseAllocationCandidate,
        ...,
    ],
) -> tuple[
    SupplierReceiptBaseAllocationTarget,
    ...,
]:
    """
    Allocate one posted receipt line's exact accounting base across
    ACTIVE InvoiceFulfillmentAllocations.

    Monetary truth:
        the posted receipt line's VAT-exclusive accounting amount.

    Quantity truth:
        receipt/fulfillment line quantity
        + ACTIVE InvoiceFulfillmentAllocation.quantity.

    Ordering:
        event_date -> InvoiceFulfillmentAllocation.id.

    Allocation uses cumulative-delta currency rounding:

        cumulative_after
            = round(
                receipt_base
                * allocated_quantity_after
                / receipt_quantity
              )

        source_amount
            = cumulative_after
            - cumulative_before

    This guarantees that full allocation closes exactly to the
    posted receipt base and avoids independent per-source rounding
    drift such as 83.33 + 83.33 + 83.33 = 249.99.
    """

    currency = _currency(
        currency_code
    )

    total_quantity = _decimal(
        receipt_quantity,
        label="Receipt quantity",
    )

    if total_quantity <= ZERO:
        raise SupplierEconomicLiabilityAmountError(
            "Receipt quantity must be greater than zero"
        )

    total_base = _money(
        receipt_base_amount,
        currency_code=currency,
        label="Receipt base",
    )

    if total_base < ZERO:
        raise SupplierEconomicLiabilityAmountError(
            "Receipt base amount cannot be negative"
        )

    validated = []

    source_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            SupplierReceiptBaseAllocationCandidate,
        ):
            raise SupplierEconomicLiabilitySourceError(
                "candidate must be "
                "SupplierReceiptBaseAllocationCandidate"
            )

        source_id = _source_id(
            candidate.source_id,
            label="Receipt allocation",
        )

        if source_id in source_ids:
            raise SupplierEconomicLiabilitySourceError(
                "Receipt allocation source_id values "
                "must be unique"
            )

        source_ids.add(
            source_id
        )

        event_date = _event_date(
            candidate.event_date,
            label="Receipt allocation",
        )

        quantity = _decimal(
            candidate.quantity,
            label="Allocation quantity",
        )

        if quantity <= ZERO:
            raise SupplierEconomicLiabilityAmountError(
                "Allocation quantity must be "
                "greater than zero"
            )

        validated.append(
            (
                source_id,
                event_date,
                quantity,
            )
        )

    ordered = tuple(
        sorted(
            validated,
            key=lambda item: (
                item[1],
                item[0],
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

    if allocated_quantity > total_quantity:
        raise SupplierEconomicLiabilityAmountError(
            "ACTIVE allocation quantity exceeds "
            "receipt quantity"
        )

    cumulative_quantity = ZERO

    cumulative_base_before = _money(
        ZERO,
        currency_code=currency,
        label="Cumulative base",
    )

    targets = []

    for (
        source_id,
        event_date,
        quantity,
    ) in ordered:
        cumulative_after = (
            cumulative_quantity
            + quantity
        )

        cumulative_base_after = (
            _cumulative_base(
                receipt_base_amount=total_base,
                cumulative_quantity=(
                    cumulative_after
                ),
                receipt_quantity=(
                    total_quantity
                ),
                currency_code=currency,
            )
        )

        source_amount = (
            cumulative_base_after
            - cumulative_base_before
        )

        if source_amount < ZERO:
            raise SupplierEconomicLiabilityAmountError(
                "Allocated receipt base cannot be negative"
            )

        targets.append(
            SupplierReceiptBaseAllocationTarget(
                source_id=source_id,
                event_date=event_date,
                amount=source_amount,
                currency_code=currency,
            )
        )

        cumulative_quantity = (
            cumulative_after
        )

        cumulative_base_before = (
            cumulative_base_after
        )

    expected_total = _cumulative_base(
        receipt_base_amount=total_base,
        cumulative_quantity=(
            cumulative_quantity
        ),
        receipt_quantity=total_quantity,
        currency_code=currency,
    )

    actual_total = sum(
        (
            target.amount
            for target in targets
        ),
        ZERO,
    )

    if actual_total != expected_total:
        raise SupplierEconomicLiabilityAmountError(
            "Receipt-base aggregate rounding "
            "invariant failed"
        )

    if (
        cumulative_quantity
        == total_quantity
        and actual_total
        != total_base
    ):
        raise SupplierEconomicLiabilityAmountError(
            "Fully allocated receipt base must "
            "equal posted receipt base"
        )

    return tuple(
        targets
    )


def build_supplier_economic_liability_candidates(
    *,
    base_targets: tuple[
        SupplierReceiptBaseAllocationTarget,
        ...,
    ],
    vat_components: tuple[
        SupplierVatLiabilityComponent,
        ...,
    ],
    currency_code: str,
) -> tuple[
    SupplierEconomicLiabilityCandidate,
    ...,
]:
    """
    Build gross supplier economic liability by allocation:

        gross liability
            = allocated posted receipt base
            + ACTIVE economic INPUT VAT bridge amount.

    Non-VAT purchases simply have no VAT component.

    Multiple VAT components for the same allocation are accepted and
    summed, allowing the loader to aggregate more than one active tax
    component without changing this pure calculation layer.

    Every VAT component must belong to an existing base source and must
    carry the same economic receipt date. A VAT bridge is not allowed to
    create supplier liability independently from receipt base.
    """

    currency = _currency(
        currency_code
    )

    base_by_source = {}

    for target in base_targets:
        if not isinstance(
            target,
            SupplierReceiptBaseAllocationTarget,
        ):
            raise SupplierEconomicLiabilitySourceError(
                "base target must be "
                "SupplierReceiptBaseAllocationTarget"
            )

        source_id = _source_id(
            target.source_id,
            label="Base",
        )

        if source_id in base_by_source:
            raise SupplierEconomicLiabilitySourceError(
                "Base source_id values must be unique"
            )

        event_date = _event_date(
            target.event_date,
            label="Base",
        )

        amount = _money(
            target.amount,
            currency_code=currency,
            label="Base",
        )

        if amount < ZERO:
            raise SupplierEconomicLiabilityAmountError(
                "Base amount cannot be negative"
            )

        if target.currency_code.upper() != currency:
            raise SupplierEconomicLiabilityCurrencyError(
                "Base target currency differs "
                "from requested currency"
            )

        base_by_source[
            source_id
        ] = (
            event_date,
            amount,
        )

    vat_by_source = {}

    for component in vat_components:
        if not isinstance(
            component,
            SupplierVatLiabilityComponent,
        ):
            raise SupplierEconomicLiabilitySourceError(
                "VAT component must be "
                "SupplierVatLiabilityComponent"
            )

        source_id = _source_id(
            component.source_id,
            label="VAT",
        )

        if source_id not in base_by_source:
            raise SupplierEconomicLiabilitySourceError(
                "VAT component has no matching "
                "receipt-base source"
            )

        component_date = _event_date(
            component.event_date,
            label="VAT",
        )

        base_date = (
            base_by_source[
                source_id
            ][0]
        )

        if component_date != base_date:
            raise SupplierEconomicLiabilitySourceError(
                "VAT component event_date must match "
                "receipt-base event_date"
            )

        amount = _money(
            component.amount,
            currency_code=currency,
            label="VAT",
        )

        if amount <= ZERO:
            raise SupplierEconomicLiabilityAmountError(
                "VAT component amount must be "
                "greater than zero"
            )

        vat_by_source[
            source_id
        ] = (
            vat_by_source.get(
                source_id,
                ZERO,
            )
            + amount
        )

    candidates = []

    for source_id in sorted(
        base_by_source,
        key=lambda value: (
            base_by_source[
                value
            ][0],
            value,
        ),
    ):
        (
            event_date,
            base_amount,
        ) = base_by_source[
            source_id
        ]

        gross_amount = _money(
            (
                base_amount
                + vat_by_source.get(
                    source_id,
                    ZERO,
                )
            ),
            currency_code=currency,
            label="Gross liability",
        )

        if gross_amount <= ZERO:
            continue

        candidates.append(
            SupplierEconomicLiabilityCandidate(
                source_id=source_id,
                event_date=event_date,
                amount=gross_amount,
            )
        )

    return tuple(
        candidates
    )
