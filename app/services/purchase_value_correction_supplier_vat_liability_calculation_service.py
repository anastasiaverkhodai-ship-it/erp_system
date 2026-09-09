from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


ZERO = Decimal("0")
CENT = Decimal("0.01")


class PurchaseValueCorrectionSupplierVatLiabilityCalculationError(
    Exception
):
    """Invalid PVC economic VAT -> supplier-liability allocation."""


class PurchaseValueCorrectionSupplierVatLiabilitySourceError(
    PurchaseValueCorrectionSupplierVatLiabilityCalculationError
):
    pass


class PurchaseValueCorrectionSupplierVatLiabilityAmountError(
    PurchaseValueCorrectionSupplierVatLiabilityCalculationError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierVatAllocationCandidate:
    """
    One current ACTIVE economic INPUT VAT fulfillment component.

    source_id is the exact InvoiceFulfillmentAllocation liability source.

    amount is the current positive economic INPUT VAT component already
    included in supplier 631 capacity before PVC VAT correction.
    """

    source_id: int
    event_date: object
    amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionSupplierVatAdjustment:
    """
    One source-level signed economic VAT adjustment.

    amount_delta:
        decrease -> negative
        increase -> positive
    """

    source_id: int
    event_date: object
    amount_delta: Decimal


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(
                value
            )
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                f"{field} must be finite"
            )
        )

    return result


def _positive_id(
    value,
    *,
    field: str,
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
        raise (
            PurchaseValueCorrectionSupplierVatLiabilitySourceError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _money(
    value,
) -> Decimal:
    return (
        _decimal(
            value,
            field="money amount",
        )
        .quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
    )


def build_purchase_value_correction_supplier_vat_adjustments(
    *,
    components: tuple[
        SupplierVatAllocationCandidate,
        ...,
    ],
    adjustment_kind: str,
    adjusted_tax_amount,
) -> tuple[
    PurchaseValueCorrectionSupplierVatAdjustment,
    ...,
]:
    """
    Allocate one ACTIVE PVC economic VAT amount over current economic
    INPUT VAT fulfillment components.

    The allocation is quantity-neutral and source-preserving.

    Weight basis:
        current INPUT VAT economic component amount per
        InvoiceFulfillmentAllocation.

    Ordering:
        event_date -> source_id.

    Rounding:
        cumulative UAH-cent rounding.

    decrease:
        signed total = -adjusted_tax_amount.

        The decrease cannot exceed current economic INPUT VAT capacity
        for this TaxCalculation.

    increase:
        signed total = +adjusted_tax_amount.

        Existing current economic VAT components provide the allocation
        weights. We deliberately fail closed if no positive component
        exists because there is then no trustworthy IFA VAT provenance
        for allocating the increase.

    Full exact conservation:
        sum(source amount_delta) == signed correction amount.

    No DB mutation.
    No JournalEntry.
    No supplier clearing persistence.
    No commit/rollback.
    """

    if adjustment_kind not in {
        "decrease",
        "increase",
    }:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilitySourceError(
                "adjustment_kind must be 'decrease' or 'increase'"
            )
        )

    correction = _money(
        adjusted_tax_amount
    )

    if correction < ZERO:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                "adjusted_tax_amount cannot be negative"
            )
        )

    if correction == ZERO:
        return ()

    normalized = []

    seen_source_ids = set()

    for candidate in components:
        if not isinstance(
            candidate,
            SupplierVatAllocationCandidate,
        ):
            raise (
                PurchaseValueCorrectionSupplierVatLiabilitySourceError(
                    "components must contain "
                    "SupplierVatAllocationCandidate"
                )
            )

        source_id = _positive_id(
            candidate.source_id,
            field="source_id",
        )

        if source_id in seen_source_ids:
            raise (
                PurchaseValueCorrectionSupplierVatLiabilitySourceError(
                    "source_id values must be unique"
                )
            )

        seen_source_ids.add(
            source_id
        )

        amount = _money(
            candidate.amount
        )

        if amount <= ZERO:
            raise (
                PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                    "current supplier VAT component amount "
                    "must be greater than zero"
                )
            )

        normalized.append(
            SupplierVatAllocationCandidate(
                source_id=source_id,
                event_date=candidate.event_date,
                amount=amount,
            )
        )

    if not normalized:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilitySourceError(
                "nonzero PVC VAT correction requires "
                "current economic INPUT VAT components"
            )
        )

    ordered = tuple(
        sorted(
            normalized,
            key=lambda candidate: (
                candidate.event_date,
                candidate.source_id,
            ),
        )
    )

    current_total = _money(
        sum(
            (
                candidate.amount
                for candidate in ordered
            ),
            ZERO,
        )
    )

    if current_total <= ZERO:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                "current economic INPUT VAT capacity "
                "must be greater than zero"
            )
        )

    if (
        adjustment_kind == "decrease"
        and correction > current_total
    ):
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                "PVC VAT decrease exceeds current economic "
                "INPUT VAT supplier-liability capacity"
            )
        )

    signed_total = (
        -correction
        if adjustment_kind == "decrease"
        else correction
    )

    result = []

    cumulative_weight = ZERO
    cumulative_allocated_abs = ZERO

    for index, candidate in enumerate(
        ordered,
    ):
        cumulative_weight += (
            candidate.amount
        )

        if index == len(ordered) - 1:
            cumulative_target_abs = correction
        else:
            cumulative_target_abs = (
                (
                    correction
                    * cumulative_weight
                    / current_total
                )
                .quantize(
                    CENT,
                    rounding=ROUND_HALF_UP,
                )
            )

        allocated_abs = (
            cumulative_target_abs
            - cumulative_allocated_abs
        )

        cumulative_allocated_abs = (
            cumulative_target_abs
        )

        if allocated_abs < ZERO:
            raise (
                PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                    "cumulative VAT allocation became negative"
                )
            )

        if allocated_abs == ZERO:
            continue

        if (
            adjustment_kind == "decrease"
            and allocated_abs > candidate.amount
        ):
            raise (
                PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                    "source-level PVC VAT decrease exceeds "
                    "current source VAT component"
                )
            )

        result.append(
            PurchaseValueCorrectionSupplierVatAdjustment(
                source_id=candidate.source_id,
                event_date=candidate.event_date,
                amount_delta=(
                    -allocated_abs
                    if adjustment_kind == "decrease"
                    else allocated_abs
                ),
            )
        )

    actual_signed_total = _money(
        sum(
            (
                item.amount_delta
                for item in result
            ),
            ZERO,
        )
    )

    if actual_signed_total != signed_total:
        raise (
            PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                "PVC VAT source allocation does not conserve "
                "the signed correction total"
            )
        )

    if adjustment_kind == "decrease":
        component_by_source = {
            candidate.source_id:
                candidate.amount
            for candidate in ordered
        }

        for item in result:
            remaining = _money(
                component_by_source[
                    item.source_id
                ]
                + item.amount_delta
            )

            if remaining < ZERO:
                raise (
                    PurchaseValueCorrectionSupplierVatLiabilityAmountError(
                        "PVC VAT decrease creates negative "
                        "source economic VAT liability"
                    )
                )

    return tuple(
        result
    )
