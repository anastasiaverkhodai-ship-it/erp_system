from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class VatAdvanceBridgeAccountingError(Exception):
    """VAT advance bridge accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class VatAdvanceBridgeAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class VatAdvanceBridgeAccountingPlan:
    amount: Decimal
    lines: tuple[
        VatAdvanceBridgeAccountingLine,
        ...,
    ]


def create_vat_advance_bridge_accounting_plan(
    *,
    amount: Decimal,
) -> VatAdvanceBridgeAccountingPlan:
    """
    Financial-accounting bridge for OUTPUT VAT already recognized
    from an advance/prepayment before Sales recognition.

        Dr GOODS_REVENUE
        Cr VAT_OUTPUT

    Generic JournalEntry reversal later swaps these lines:

        Dr VAT_OUTPUT
        Cr GOODS_REVENUE
    """

    if not isinstance(
        amount,
        Decimal,
    ):
        raise VatAdvanceBridgeAccountingError(
            "VAT advance bridge accounting amount "
            "must be Decimal"
        )

    if not amount.is_finite():
        raise VatAdvanceBridgeAccountingError(
            "VAT advance bridge accounting amount "
            "must be finite"
        )

    if amount <= ZERO:
        raise VatAdvanceBridgeAccountingError(
            "VAT advance bridge accounting amount "
            "must be greater than zero"
        )

    lines = (
        VatAdvanceBridgeAccountingLine(
            role=(
                AccountingAccountRole
                .GOODS_REVENUE
            ),
            debit=amount,
            credit=ZERO,
        ),
        VatAdvanceBridgeAccountingLine(
            role=(
                AccountingAccountRole
                .VAT_OUTPUT
            ),
            debit=ZERO,
            credit=amount,
        ),
    )

    return VatAdvanceBridgeAccountingPlan(
        amount=amount,
        lines=lines,
    )


def required_roles_for_vat_advance_bridge_plan(
    plan: VatAdvanceBridgeAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        VatAdvanceBridgeAccountingPlan,
    ):
        raise VatAdvanceBridgeAccountingError(
            "VAT advance bridge accounting plan "
            "has invalid type"
        )

    roles = []

    for line in plan.lines:
        if line.role not in roles:
            roles.append(
                line.role
            )

    return tuple(
        roles
    )
