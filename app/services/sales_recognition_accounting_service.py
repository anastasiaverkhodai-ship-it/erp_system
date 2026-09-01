from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class SalesRecognitionAccountingError(
    Exception
):
    """Base Sales recognition accounting-plan error."""


class SalesRecognitionAccountingAmountError(
    SalesRecognitionAccountingError
):
    """Sales recognition amount is invalid for GL posting."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionAccountingLinePlan:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionAccountingPlan:
    lines: tuple[
        SalesRecognitionAccountingLinePlan,
        ...,
    ]


def money(
    amount: Decimal,
) -> Decimal:
    return Decimal(
        str(amount)
    ).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _positive_money(
    amount: Decimal,
) -> Decimal:
    normalized = money(
        amount
    )

    if normalized <= ZERO:
        raise SalesRecognitionAccountingAmountError(
            "Sales recognition journal amount "
            "must be greater than zero"
        )

    return normalized


def create_sales_recognition_accounting_plan(
    *,
    amount: Decimal,
) -> SalesRecognitionAccountingPlan:
    """
    Commercial Sales recognition at gross Invoice value:

        Dr CUSTOMER_RECEIVABLES
        Cr GOODS_REVENUE

    OUTPUT VAT recognition remains a separate accounting
    layer and is not included in this plan.
    """

    amount = _positive_money(
        amount
    )

    return SalesRecognitionAccountingPlan(
        lines=(
            SalesRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .CUSTOMER_RECEIVABLES
                ),
                debit=amount,
                credit=ZERO,
            ),
            SalesRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .GOODS_REVENUE
                ),
                debit=ZERO,
                credit=amount,
            ),
        )
    )


def required_roles_for_sales_recognition_plan(
    plan: SalesRecognitionAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        SalesRecognitionAccountingPlan,
    ):
        raise TypeError(
            "plan must be "
            "SalesRecognitionAccountingPlan"
        )

    return tuple(
        dict.fromkeys(
            line.role
            for line
            in plan.lines
        )
    )
