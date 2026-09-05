from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class PurchaseReturnRecognitionAccountingError(
    Exception
):
    """Purchase Return accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnRecognitionAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnRecognitionAccountingPlan:
    lines: tuple[
        PurchaseReturnRecognitionAccountingLine,
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
        raise PurchaseReturnRecognitionAccountingError(
            "Purchase Return Recognition accounting "
            "amount must be a valid Decimal"
        ) from exc

    if not value.is_finite():
        raise PurchaseReturnRecognitionAccountingError(
            "Purchase Return Recognition accounting "
            "amount must be finite"
        )

    if value <= ZERO:
        raise PurchaseReturnRecognitionAccountingError(
            "Purchase Return Recognition accounting "
            "amount must be greater than zero"
        )

    return value


def create_purchase_return_recognition_accounting_plan(
    *,
    amount,
) -> PurchaseReturnRecognitionAccountingPlan:
    """
    Account for the VAT-exclusive economic base of a purchase return.

    Original:

        Dr SUPPLIER_PAYABLES
        Cr INVENTORY_GOODS

    GENERAL 291:

        Dr 631
        Cr 281

    Generic JournalEntry reversal later produces:

        Dr INVENTORY_GOODS
        Cr SUPPLIER_PAYABLES

    GENERAL 291:

        Dr 281
        Cr 631

    amount is PurchaseReturnRecognitionEvent.returned_base_amount.

    Commercial gross and tax snapshots do not participate in this
    accounting plan. INPUT VAT / RK remains a separate lifecycle.
    """
    value = _amount_decimal(
        amount
    )

    return PurchaseReturnRecognitionAccountingPlan(
        lines=(
            PurchaseReturnRecognitionAccountingLine(
                role=(
                    AccountingAccountRole
                    .SUPPLIER_PAYABLES
                ),
                debit=value,
                credit=ZERO,
            ),
            PurchaseReturnRecognitionAccountingLine(
                role=(
                    AccountingAccountRole
                    .INVENTORY_GOODS
                ),
                debit=ZERO,
                credit=value,
            ),
        )
    )


def required_roles_for_purchase_return_recognition_plan(
    plan: PurchaseReturnRecognitionAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        PurchaseReturnRecognitionAccountingPlan,
    ):
        raise PurchaseReturnRecognitionAccountingError(
            "Purchase Return Recognition accounting "
            "plan has invalid type"
        )

    if not plan.lines:
        raise PurchaseReturnRecognitionAccountingError(
            "Purchase Return Recognition accounting "
            "plan must contain lines"
        )

    roles: list[
        AccountingAccountRole
    ] = []

    for line in plan.lines:
        if not isinstance(
            line,
            PurchaseReturnRecognitionAccountingLine,
        ):
            raise PurchaseReturnRecognitionAccountingError(
                "Purchase Return Recognition accounting "
                "plan contains invalid line"
            )

        if line.role not in roles:
            roles.append(
                line.role
            )

    return tuple(
        roles
    )
