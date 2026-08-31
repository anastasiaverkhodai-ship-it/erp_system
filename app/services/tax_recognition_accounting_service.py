from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)
from enum import StrEnum

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class TaxRecognitionAccountingError(Exception):
    """Base error for tax-recognition accounting plans."""


class TaxRecognitionAccountingAmountError(
    TaxRecognitionAccountingError
):
    """Recognition amount is invalid for GL posting."""


class TaxRecognitionAccountingSourceError(
    TaxRecognitionAccountingError
):
    """Recognition source is unsupported for this accounting plan."""


class OutputVatRecognitionSourceKind(StrEnum):
    FULFILLMENT = "fulfillment"
    SETTLEMENT = "settlement"


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionAccountingLinePlan:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionAccountingPlan:
    lines: tuple[
        TaxRecognitionAccountingLinePlan,
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
        raise TaxRecognitionAccountingAmountError(
            "OUTPUT VAT journal amount "
            "must be greater than zero"
        )

    return normalized


def create_output_vat_recognition_accounting_plan(
    *,
    source_kind: OutputVatRecognitionSourceKind,
    amount: Decimal,
) -> TaxRecognitionAccountingPlan:
    if not isinstance(
        source_kind,
        OutputVatRecognitionSourceKind,
    ):
        raise TaxRecognitionAccountingSourceError(
            "source_kind must be "
            "OutputVatRecognitionSourceKind"
        )

    amount = _positive_money(
        amount
    )

    if (
        source_kind
        == OutputVatRecognitionSourceKind.FULFILLMENT
    ):
        return TaxRecognitionAccountingPlan(
            lines=(
                TaxRecognitionAccountingLinePlan(
                    role=(
                        AccountingAccountRole
                        .GOODS_REVENUE
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                TaxRecognitionAccountingLinePlan(
                    role=(
                        AccountingAccountRole
                        .TAX_SETTLEMENT
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    if (
        source_kind
        == OutputVatRecognitionSourceKind.SETTLEMENT
    ):
        return TaxRecognitionAccountingPlan(
            lines=(
                TaxRecognitionAccountingLinePlan(
                    role=(
                        AccountingAccountRole
                        .VAT_OUTPUT
                    ),
                    debit=amount,
                    credit=ZERO,
                ),
                TaxRecognitionAccountingLinePlan(
                    role=(
                        AccountingAccountRole
                        .TAX_SETTLEMENT
                    ),
                    debit=ZERO,
                    credit=amount,
                ),
            )
        )

    raise TaxRecognitionAccountingSourceError(
        "Unsupported OUTPUT VAT "
        "recognition source"
    )


def required_roles_for_output_vat_plan(
    plan: TaxRecognitionAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        TaxRecognitionAccountingPlan,
    ):
        raise TypeError(
            "plan must be "
            "TaxRecognitionAccountingPlan"
        )

    return tuple(
        dict.fromkeys(
            line.role
            for line
            in plan.lines
        )
    )
