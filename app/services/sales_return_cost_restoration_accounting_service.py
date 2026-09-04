from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class SalesReturnCostRestorationAccountingError(
    Exception
):
    """Invalid Sales Return COGS-restoration accounting input."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostRestorationAccountingLinePlan:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostRestorationAccountingPlan:
    amount: Decimal
    lines: tuple[
        SalesReturnCostRestorationAccountingLinePlan,
        ...,
    ]


def _amount(
    value,
) -> Decimal:
    try:
        result = Decimal(
            str(
                value
            )
        )
    except Exception as exc:
        raise SalesReturnCostRestorationAccountingError(
            "Cost-restoration amount must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise SalesReturnCostRestorationAccountingError(
            "Cost-restoration amount must be finite"
        )

    if result <= ZERO:
        raise SalesReturnCostRestorationAccountingError(
            "Cost-restoration amount must be greater than zero"
        )

    return result


def create_sales_return_cost_restoration_accounting_plan(
    amount,
) -> SalesReturnCostRestorationAccountingPlan:
    """
    Accounting reversal of original sales COGS:

        Dr INVENTORY_GOODS
        Cr GOODS_COGS

    For GENERAL 291 this resolves to:

        Dr 281
        Cr 902

    VAT is intentionally outside this plan.
    """

    normalized = _amount(
        amount
    )

    return SalesReturnCostRestorationAccountingPlan(
        amount=normalized,
        lines=(
            SalesReturnCostRestorationAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .INVENTORY_GOODS
                ),
                debit=normalized,
                credit=ZERO,
            ),
            SalesReturnCostRestorationAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .GOODS_COGS
                ),
                debit=ZERO,
                credit=normalized,
            ),
        ),
    )


def required_roles_for_sales_return_cost_restoration_plan(
    plan: SalesReturnCostRestorationAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    return tuple(
        dict.fromkeys(
            line.role
            for line in plan.lines
        )
    )
