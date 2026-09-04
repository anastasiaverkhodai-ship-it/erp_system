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


class SalesReturnRecognitionAccountingError(
    Exception
):
    """Base Sales Return economic accounting error."""


class SalesReturnRecognitionAccountingAmountError(
    SalesReturnRecognitionAccountingError
):
    """Sales Return economic amount is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionAccountingLinePlan:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionAccountingPlan:
    lines: tuple[
        SalesReturnRecognitionAccountingLinePlan,
        ...,
    ]


def money(
    amount: Decimal,
) -> Decimal:
    return Decimal(
        str(
            amount
        )
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
        raise (
            SalesReturnRecognitionAccountingAmountError(
                "Sales Return recognition journal "
                "amount must be greater than zero"
            )
        )

    return normalized


def create_sales_return_recognition_accounting_plan(
    *,
    amount: Decimal,
) -> SalesReturnRecognitionAccountingPlan:
    """
    Economic Sales Return at gross commercial value:

        Dr SALES_DEDUCTIONS
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291 design:

        Dr 704
        Cr 361

    VAT/RK recognition remains a separate tax-accounting layer.
    No VAT account is part of this plan.
    """

    amount = _positive_money(
        amount
    )

    return SalesReturnRecognitionAccountingPlan(
        lines=(
            SalesReturnRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .SALES_DEDUCTIONS
                ),
                debit=amount,
                credit=ZERO,
            ),
            SalesReturnRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .CUSTOMER_RECEIVABLES
                ),
                debit=ZERO,
                credit=amount,
            ),
        )
    )


def required_roles_for_sales_return_recognition_plan(
    plan: SalesReturnRecognitionAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        SalesReturnRecognitionAccountingPlan,
    ):
        raise TypeError(
            "plan must be "
            "SalesReturnRecognitionAccountingPlan"
        )

    return tuple(
        dict.fromkeys(
            line.role
            for line in plan.lines
        )
    )
