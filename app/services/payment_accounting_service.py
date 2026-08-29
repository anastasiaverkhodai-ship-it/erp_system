from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.payment_types import (
    PaymentDirection,
)


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class PaymentAccountingError(
    Exception
):
    pass


class PaymentAccountingDirectionError(
    PaymentAccountingError
):
    pass


class PaymentAccountingAmountError(
    PaymentAccountingError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PaymentAccountingLinePlan:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PaymentAccountingPlan:
    lines: tuple[
        PaymentAccountingLinePlan,
        ...
    ]


def money(
    value: Decimal,
) -> Decimal:
    return Decimal(
        value
    ).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _positive_money(
    value: Decimal,
) -> Decimal:
    amount = money(
        value
    )

    if amount <= ZERO:
        raise PaymentAccountingAmountError(
            "Accounting amount must be "
            "greater than zero"
        )

    return amount


def create_payment_confirmation_accounting_plan(
    *,
    direction: PaymentDirection,
    amount: Decimal,
) -> PaymentAccountingPlan:
    amount = _positive_money(
        amount
    )

    if (
        direction
        == PaymentDirection.INCOMING
    ):
        return PaymentAccountingPlan(
            lines=(
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.BANK_CURRENT_UAH
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.CUSTOMER_ADVANCES
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    if (
        direction
        == PaymentDirection.OUTGOING
    ):
        return PaymentAccountingPlan(
            lines=(
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.SUPPLIER_ADVANCES
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.BANK_CURRENT_UAH
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    raise PaymentAccountingDirectionError(
        "Unsupported Payment direction"
    )


def create_settlement_accounting_plan(
    *,
    direction: PaymentDirection,
    amount: Decimal,
) -> PaymentAccountingPlan:
    amount = _positive_money(
        amount
    )

    if (
        direction
        == PaymentDirection.INCOMING
    ):
        return PaymentAccountingPlan(
            lines=(
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.CUSTOMER_ADVANCES
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.CUSTOMER_RECEIVABLES
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    if (
        direction
        == PaymentDirection.OUTGOING
    ):
        return PaymentAccountingPlan(
            lines=(
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.SUPPLIER_PAYABLES
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                PaymentAccountingLinePlan(
                    role=(
                        AccountingAccountRole.SUPPLIER_ADVANCES
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    raise PaymentAccountingDirectionError(
        "Unsupported Payment direction"
    )


def required_roles_for_plan(
    plan: PaymentAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...
]:
    return tuple(
        dict.fromkeys(
            line.role
            for line
            in plan.lines
        )
    )
