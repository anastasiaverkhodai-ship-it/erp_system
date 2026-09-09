from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")

ON_HAND = "on_hand"
ISSUED = "issued"


class PurchaseValueCorrectionMovingAverageAccountingError(
    Exception
):
    """Base PVC moving-average accounting-plan error."""


class PurchaseValueCorrectionMovingAverageAccountingAmountError(
    PurchaseValueCorrectionMovingAverageAccountingError
):
    """PVC moving-average accounting amount is invalid."""


class PurchaseValueCorrectionMovingAverageAccountingDestinationError(
    PurchaseValueCorrectionMovingAverageAccountingError
):
    """PVC moving-average accounting destination is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageAccountingLinePlan:
    role: AccountingAccountRole | None
    destination_kind: str | None
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageAccountingPlan:
    delta: Decimal
    amount: Decimal
    destination_kind: str
    lines: tuple[
        PurchaseValueCorrectionMovingAverageAccountingLinePlan,
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
            PurchaseValueCorrectionMovingAverageAccountingAmountError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionMovingAverageAccountingAmountError(
            f"{field} must be finite"
        )

    return result


def create_purchase_value_correction_moving_average_accounting_plan(
    *,
    original_valuation_amount,
    corrected_valuation_amount,
    destination_kind: str,
) -> PurchaseValueCorrectionMovingAverageAccountingPlan:
    """
    Pure GL direction for one immutable PVC moving-average replay event.

    delta = corrected valuation - original valuation.

    on_hand:
        destination is INVENTORY_GOODS.

    issued:
        destination is the exact historical ISSUE cost account.
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
        original_valuation_amount,
        field="original_valuation_amount",
    )
    corrected = _decimal(
        corrected_valuation_amount,
        field="corrected_valuation_amount",
    )

    if original < ZERO or corrected < ZERO:
        raise PurchaseValueCorrectionMovingAverageAccountingAmountError(
            "PVC moving-average valuation amounts cannot be negative"
        )

    if destination_kind not in {
        ON_HAND,
        ISSUED,
    }:
        raise (
            PurchaseValueCorrectionMovingAverageAccountingDestinationError(
                "destination_kind must be on_hand or issued"
            )
        )

    delta = corrected - original

    if delta == ZERO:
        raise PurchaseValueCorrectionMovingAverageAccountingAmountError(
            "PVC moving-average accounting delta cannot be zero"
        )

    amount = abs(delta)

    destination_role = (
        AccountingAccountRole.INVENTORY_GOODS
        if destination_kind == ON_HAND
        else None
    )

    destination_line = (
        PurchaseValueCorrectionMovingAverageAccountingLinePlan(
            role=destination_role,
            destination_kind=destination_kind,
            debit=amount if delta > ZERO else ZERO,
            credit=amount if delta < ZERO else ZERO,
        )
    )

    supplier_line = (
        PurchaseValueCorrectionMovingAverageAccountingLinePlan(
            role=AccountingAccountRole.SUPPLIER_PAYABLES,
            destination_kind=None,
            debit=amount if delta < ZERO else ZERO,
            credit=amount if delta > ZERO else ZERO,
        )
    )

    return PurchaseValueCorrectionMovingAverageAccountingPlan(
        delta=delta,
        amount=amount,
        destination_kind=destination_kind,
        lines=(
            destination_line,
            supplier_line,
        ),
    )


def required_roles_for_purchase_value_correction_moving_average_plan(
    plan: PurchaseValueCorrectionMovingAverageAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        PurchaseValueCorrectionMovingAverageAccountingPlan,
    ):
        raise TypeError(
            "plan must be "
            "PurchaseValueCorrectionMovingAverageAccountingPlan"
        )

    return tuple(
        dict.fromkeys(
            line.role
            for line in plan.lines
            if line.role is not None
        )
    )
