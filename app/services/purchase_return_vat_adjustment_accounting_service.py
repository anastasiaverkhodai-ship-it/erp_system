from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class PurchaseReturnVatAdjustmentAccountingError(
    Exception
):
    """Purchase Return VAT adjustment accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAdjustmentAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAdjustmentAccountingPlan:
    lines: tuple[
        PurchaseReturnVatAdjustmentAccountingLine,
        ...,
    ]


def _amount_decimal(
    amount,
) -> Decimal:
    try:
        value = Decimal(
            str(
                amount
            )
        )
    except Exception as exc:
        raise (
            PurchaseReturnVatAdjustmentAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be a valid Decimal"
            )
        ) from exc

    if not value.is_finite():
        raise (
            PurchaseReturnVatAdjustmentAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be finite"
            )
        )

    if value <= ZERO:
        raise (
            PurchaseReturnVatAdjustmentAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be greater than zero"
            )
        )

    return value


def create_purchase_return_vat_adjustment_accounting_plan(
    *,
    amount,
) -> PurchaseReturnVatAdjustmentAccountingPlan:
    """
    Purchase Return economic INPUT VAT decrease.

    Original:
        Dr SUPPLIER_PAYABLES
        Cr VAT_INPUT

    GENERAL 291:
        Dr 631
        Cr 644

    Generic reversal:
        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

    amount = adjusted_tax_amount.

    adjusted_taxable_base never drives the JE amount.
    """

    value = _amount_decimal(
        amount
    )

    return PurchaseReturnVatAdjustmentAccountingPlan(
        lines=(
            PurchaseReturnVatAdjustmentAccountingLine(
                role=(
                    AccountingAccountRole
                    .SUPPLIER_PAYABLES
                ),
                debit=value,
                credit=ZERO,
            ),
            PurchaseReturnVatAdjustmentAccountingLine(
                role=(
                    AccountingAccountRole
                    .VAT_INPUT
                ),
                debit=ZERO,
                credit=value,
            ),
        )
    )


def required_roles_for_purchase_return_vat_adjustment_plan(
    plan: PurchaseReturnVatAdjustmentAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        PurchaseReturnVatAdjustmentAccountingPlan,
    ):
        raise (
            PurchaseReturnVatAdjustmentAccountingError(
                "Invalid Purchase Return VAT adjustment plan"
            )
        )

    if not plan.lines:
        raise (
            PurchaseReturnVatAdjustmentAccountingError(
                "Purchase Return VAT adjustment plan "
                "must contain lines"
            )
        )

    result = []

    for line in plan.lines:
        if not isinstance(
            line,
            PurchaseReturnVatAdjustmentAccountingLine,
        ):
            raise (
                PurchaseReturnVatAdjustmentAccountingError(
                    "Purchase Return VAT adjustment plan "
                    "contains invalid line"
                )
            )

        if line.role not in result:
            result.append(
                line.role
            )

    return tuple(
        result
    )
