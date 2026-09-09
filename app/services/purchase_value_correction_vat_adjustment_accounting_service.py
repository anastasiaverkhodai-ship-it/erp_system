from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionVatAdjustmentAccountingError(
    Exception
):
    pass


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionVatAdjustmentAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionVatAdjustmentAccountingPlan:
    lines: tuple[
        PurchaseValueCorrectionVatAdjustmentAccountingLine,
        ...,
    ]


def _amount_decimal(amount) -> Decimal:
    try:
        value = Decimal(str(amount))
    except Exception as exc:
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "PVC VAT adjustment accounting amount "
            "must be a valid Decimal"
        ) from exc

    if not value.is_finite():
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "PVC VAT adjustment accounting amount must be finite"
        )

    if value <= ZERO:
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "PVC VAT adjustment accounting amount "
            "must be greater than zero"
        )

    return value


def create_purchase_value_correction_vat_adjustment_accounting_plan(
    *,
    amount,
    adjustment_kind: str,
) -> PurchaseValueCorrectionVatAdjustmentAccountingPlan:
    """
    Economic buyer-side VAT correction.

    decrease:
        Dr SUPPLIER_PAYABLES
        Cr VAT_INPUT

    increase:
        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES
    """
    value = _amount_decimal(amount)

    if adjustment_kind == "decrease":
        lines = (
            PurchaseValueCorrectionVatAdjustmentAccountingLine(
                role=AccountingAccountRole.SUPPLIER_PAYABLES,
                debit=value,
                credit=ZERO,
            ),
            PurchaseValueCorrectionVatAdjustmentAccountingLine(
                role=AccountingAccountRole.VAT_INPUT,
                debit=ZERO,
                credit=value,
            ),
        )
    elif adjustment_kind == "increase":
        lines = (
            PurchaseValueCorrectionVatAdjustmentAccountingLine(
                role=AccountingAccountRole.VAT_INPUT,
                debit=value,
                credit=ZERO,
            ),
            PurchaseValueCorrectionVatAdjustmentAccountingLine(
                role=AccountingAccountRole.SUPPLIER_PAYABLES,
                debit=ZERO,
                credit=value,
            ),
        )
    else:
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "adjustment_kind must be 'decrease' or 'increase'"
        )

    return PurchaseValueCorrectionVatAdjustmentAccountingPlan(
        lines=lines
    )


def required_roles_for_purchase_value_correction_vat_adjustment_plan(
    plan: PurchaseValueCorrectionVatAdjustmentAccountingPlan,
) -> tuple[AccountingAccountRole, ...]:
    if not isinstance(
        plan,
        PurchaseValueCorrectionVatAdjustmentAccountingPlan,
    ):
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "Invalid PVC VAT adjustment accounting plan"
        )

    if not plan.lines:
        raise PurchaseValueCorrectionVatAdjustmentAccountingError(
            "PVC VAT adjustment accounting plan must contain lines"
        )

    result = []
    for line in plan.lines:
        if not isinstance(
            line,
            PurchaseValueCorrectionVatAdjustmentAccountingLine,
        ):
            raise PurchaseValueCorrectionVatAdjustmentAccountingError(
                "PVC VAT adjustment accounting plan "
                "contains invalid line"
            )

        if line.role not in result:
            result.append(line.role)

    return tuple(result)
