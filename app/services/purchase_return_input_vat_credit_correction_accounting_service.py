from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class PurchaseReturnInputVatCreditCorrectionAccountingError(
    Exception
):
    """Purchase Return VAT adjustment accounting plan is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInputVatCreditCorrectionAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInputVatCreditCorrectionAccountingPlan:
    lines: tuple[
        PurchaseReturnInputVatCreditCorrectionAccountingLine,
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
            PurchaseReturnInputVatCreditCorrectionAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be a valid Decimal"
            )
        ) from exc

    if not value.is_finite():
        raise (
            PurchaseReturnInputVatCreditCorrectionAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be finite"
            )
        )

    if value <= ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionAccountingError(
                "Purchase Return VAT adjustment accounting "
                "amount must be greater than zero"
            )
        )

    return value


def create_purchase_return_input_vat_credit_correction_accounting_plan(
    *,
    amount,
) -> PurchaseReturnInputVatCreditCorrectionAccountingPlan:
    """
    Purchase Return legal INPUT VAT credit decrease.

    Original:
        Dr VAT_INPUT
        Cr TAX_SETTLEMENT

    GENERAL 291:
        Dr 644
        Cr 641

    Generic reversal:
        Dr TAX_SETTLEMENT
        Cr VAT_INPUT

    amount = reduced_tax_amount.

    reduced_taxable_base never drives the JE amount.
    """

    value = _amount_decimal(
        amount
    )

    return PurchaseReturnInputVatCreditCorrectionAccountingPlan(
        lines=(
            PurchaseReturnInputVatCreditCorrectionAccountingLine(
                role=(
                    AccountingAccountRole
                    .VAT_INPUT
                ),
                debit=value,
                credit=ZERO,
            ),
            PurchaseReturnInputVatCreditCorrectionAccountingLine(
                role=(
                    AccountingAccountRole
                    .TAX_SETTLEMENT
                ),
                debit=ZERO,
                credit=value,
            ),
        )
    )


def required_roles_for_purchase_return_input_vat_credit_correction_plan(
    plan: PurchaseReturnInputVatCreditCorrectionAccountingPlan,
) -> tuple[
    AccountingAccountRole,
    ...,
]:
    if not isinstance(
        plan,
        PurchaseReturnInputVatCreditCorrectionAccountingPlan,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionAccountingError(
                "Invalid Purchase Return VAT adjustment plan"
            )
        )

    if not plan.lines:
        raise (
            PurchaseReturnInputVatCreditCorrectionAccountingError(
                "Purchase Return VAT adjustment plan "
                "must contain lines"
            )
        )

    result = []

    for line in plan.lines:
        if not isinstance(
            line,
            PurchaseReturnInputVatCreditCorrectionAccountingLine,
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionAccountingError(
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
