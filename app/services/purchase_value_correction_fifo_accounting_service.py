from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")
ON_HAND = "on_hand"
ISSUED = "issued"


class PurchaseValueCorrectionFifoAccountingError(
    Exception
):
    """Base PVC FIFO accounting-plan error."""


class PurchaseValueCorrectionFifoAccountingAmountError(
    PurchaseValueCorrectionFifoAccountingError
):
    """PVC FIFO accounting amount is invalid."""


class PurchaseValueCorrectionFifoAccountingDestinationError(
    PurchaseValueCorrectionFifoAccountingError
):
    """PVC FIFO accounting destination is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoAccountingLinePlan:
    role: AccountingAccountRole | None
    destination_kind: str | None
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoAccountingPlan:
    delta: Decimal
    amount: Decimal
    destination_kind: str
    lines: tuple[
        PurchaseValueCorrectionFifoAccountingLinePlan,
        ...,
    ]


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionFifoAccountingAmountError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionFifoAccountingAmountError(
            f"{field} must be finite"
        )

    return result


def create_purchase_value_correction_fifo_accounting_plan(
    *,
    original_base_amount,
    corrected_base_amount,
    destination_kind: str,
) -> PurchaseValueCorrectionFifoAccountingPlan:
    """
    Pure GL direction for one immutable PVC FIFO impact.

    delta = corrected - original

    on_hand:
        destination is INVENTORY_GOODS.

    issued:
        destination is the historical ISSUE cost account.
        Resolution is deliberately deferred to the journal layer.

    delta > 0:
        Dr destination
        Cr SUPPLIER_PAYABLES

    delta < 0:
        Dr SUPPLIER_PAYABLES
        Cr destination

    No hardcoded chart-of-account numbers.
    """

    original = _decimal(
        original_base_amount,
        field="original_base_amount",
    )

    corrected = _decimal(
        corrected_base_amount,
        field="corrected_base_amount",
    )

    if original < ZERO or corrected < ZERO:
        raise PurchaseValueCorrectionFifoAccountingAmountError(
            "PVC FIFO base amounts cannot be negative"
        )

    if destination_kind not in {
        ON_HAND,
        ISSUED,
    }:
        raise PurchaseValueCorrectionFifoAccountingDestinationError(
            "destination_kind must be on_hand or issued"
        )

    delta = corrected - original

    if delta == ZERO:
        raise PurchaseValueCorrectionFifoAccountingAmountError(
            "PVC FIFO accounting delta cannot be zero"
        )

    amount = abs(delta)

    destination_role = (
        AccountingAccountRole.INVENTORY_GOODS
        if destination_kind == ON_HAND
        else None
    )

    destination_line = (
        PurchaseValueCorrectionFifoAccountingLinePlan(
            role=destination_role,
            destination_kind=destination_kind,
            debit=amount if delta > ZERO else ZERO,
            credit=amount if delta < ZERO else ZERO,
        )
    )

    supplier_line = (
        PurchaseValueCorrectionFifoAccountingLinePlan(
            role=AccountingAccountRole.SUPPLIER_PAYABLES,
            destination_kind=None,
            debit=amount if delta < ZERO else ZERO,
            credit=amount if delta > ZERO else ZERO,
        )
    )

    return PurchaseValueCorrectionFifoAccountingPlan(
        delta=delta,
        amount=amount,
        destination_kind=destination_kind,
        lines=(
            destination_line,
            supplier_line,
        ),
    )


def required_roles_for_purchase_value_correction_fifo_plan(
    plan: PurchaseValueCorrectionFifoAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        PurchaseValueCorrectionFifoAccountingPlan,
    ):
        raise TypeError(
            "plan must be "
            "PurchaseValueCorrectionFifoAccountingPlan"
        )

    return tuple(
        dict.fromkeys(
            line.role
            for line in plan.lines
            if line.role is not None
        )
    )
