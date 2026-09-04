from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class CustomerAdvanceClearingAccountingError(
    Exception
):
    """Customer advance clearing accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceClearingAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceClearingAccountingPlan:
    lines: tuple[
        CustomerAdvanceClearingAccountingLine,
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
            CustomerAdvanceClearingAccountingError(
                "Customer advance clearing accounting "
                "amount must be a valid Decimal"
            )
        ) from exc

    if not value.is_finite():
        raise (
            CustomerAdvanceClearingAccountingError(
                "Customer advance clearing accounting "
                "amount must be finite"
            )
        )

    if value <= ZERO:
        raise (
            CustomerAdvanceClearingAccountingError(
                "Customer advance clearing accounting "
                "amount must be greater than zero"
            )
        )

    return value


def create_customer_advance_clearing_accounting_plan(
    *,
    amount,
) -> CustomerAdvanceClearingAccountingPlan:
    """
    Clear an issued supplier advance against economic
    supplier liability.

    Original:
        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291:
        Dr 631
        Cr 371

    Reversal is produced by the generic JournalEntry
    reversal mechanism:
        Dr CUSTOMER_RECEIVABLES
        Cr CUSTOMER_ADVANCES

    GENERAL 291:
        Dr 371
        Cr 631
    """

    value = _amount_decimal(
        amount
    )

    return (
        CustomerAdvanceClearingAccountingPlan(
            lines=(
                CustomerAdvanceClearingAccountingLine(
                    role=(
                        AccountingAccountRole
                        .CUSTOMER_ADVANCES
                    ),
                    debit=value,
                    credit=ZERO,
                ),
                CustomerAdvanceClearingAccountingLine(
                    role=(
                        AccountingAccountRole
                        .CUSTOMER_RECEIVABLES
                    ),
                    debit=ZERO,
                    credit=value,
                ),
            )
        )
    )


def required_roles_for_customer_advance_clearing_plan(
    plan: CustomerAdvanceClearingAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        CustomerAdvanceClearingAccountingPlan,
    ):
        raise (
            CustomerAdvanceClearingAccountingError(
                "Customer advance clearing accounting "
                "plan has invalid type"
            )
        )

    if not plan.lines:
        raise (
            CustomerAdvanceClearingAccountingError(
                "Customer advance clearing accounting "
                "plan must contain lines"
            )
        )

    roles = []

    for line in plan.lines:
        if not isinstance(
            line,
            CustomerAdvanceClearingAccountingLine,
        ):
            raise (
                CustomerAdvanceClearingAccountingError(
                    "Customer advance clearing accounting "
                    "plan contains invalid line"
                )
            )

        if line.role not in roles:
            roles.append(
                line.role
            )

    return tuple(
        roles
    )
