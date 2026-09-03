from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class SupplierAdvanceClearingAccountingError(
    Exception
):
    """Supplier advance clearing accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierAdvanceClearingAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierAdvanceClearingAccountingPlan:
    lines: tuple[
        SupplierAdvanceClearingAccountingLine,
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
            SupplierAdvanceClearingAccountingError(
                "Supplier advance clearing accounting "
                "amount must be a valid Decimal"
            )
        ) from exc

    if not value.is_finite():
        raise (
            SupplierAdvanceClearingAccountingError(
                "Supplier advance clearing accounting "
                "amount must be finite"
            )
        )

    if value <= ZERO:
        raise (
            SupplierAdvanceClearingAccountingError(
                "Supplier advance clearing accounting "
                "amount must be greater than zero"
            )
        )

    return value


def create_supplier_advance_clearing_accounting_plan(
    *,
    amount,
) -> SupplierAdvanceClearingAccountingPlan:
    """
    Clear an issued supplier advance against economic
    supplier liability.

    Original:
        Dr SUPPLIER_PAYABLES
        Cr SUPPLIER_ADVANCES

    GENERAL 291:
        Dr 631
        Cr 371

    Reversal is produced by the generic JournalEntry
    reversal mechanism:
        Dr SUPPLIER_ADVANCES
        Cr SUPPLIER_PAYABLES

    GENERAL 291:
        Dr 371
        Cr 631
    """

    value = _amount_decimal(
        amount
    )

    return (
        SupplierAdvanceClearingAccountingPlan(
            lines=(
                SupplierAdvanceClearingAccountingLine(
                    role=(
                        AccountingAccountRole
                        .SUPPLIER_PAYABLES
                    ),
                    debit=value,
                    credit=ZERO,
                ),
                SupplierAdvanceClearingAccountingLine(
                    role=(
                        AccountingAccountRole
                        .SUPPLIER_ADVANCES
                    ),
                    debit=ZERO,
                    credit=value,
                ),
            )
        )
    )


def required_roles_for_supplier_advance_clearing_plan(
    plan: SupplierAdvanceClearingAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        SupplierAdvanceClearingAccountingPlan,
    ):
        raise (
            SupplierAdvanceClearingAccountingError(
                "Supplier advance clearing accounting "
                "plan has invalid type"
            )
        )

    if not plan.lines:
        raise (
            SupplierAdvanceClearingAccountingError(
                "Supplier advance clearing accounting "
                "plan must contain lines"
            )
        )

    roles = []

    for line in plan.lines:
        if not isinstance(
            line,
            SupplierAdvanceClearingAccountingLine,
        ):
            raise (
                SupplierAdvanceClearingAccountingError(
                    "Supplier advance clearing accounting "
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
