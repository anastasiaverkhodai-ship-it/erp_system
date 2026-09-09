from dataclasses import dataclass
from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
    Exception
):
    pass


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine:
    role: AccountingAccountRole
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionInputVatCreditCorrectionAccountingPlan:
    lines: tuple[
        PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine,
        ...,
    ]


def _amount_decimal(amount) -> Decimal:
    try:
        value = Decimal(str(amount))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "PVC INPUT VAT credit correction accounting "
                "amount must be a valid Decimal"
            )
        ) from exc

    if not value.is_finite():
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "PVC INPUT VAT credit correction accounting "
                "amount must be finite"
            )
        )

    if value <= ZERO:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "PVC INPUT VAT credit correction accounting "
                "amount must be greater than zero"
            )
        )

    return value


def create_purchase_value_correction_input_vat_credit_correction_accounting_plan(
    *,
    amount,
    correction_kind: str,
) -> PurchaseValueCorrectionInputVatCreditCorrectionAccountingPlan:
    """
    Legal buyer-side INPUT VAT credit correction.

    decrease:
        Dr VAT_INPUT
        Cr TAX_SETTLEMENT

    increase:
        Dr TAX_SETTLEMENT
        Cr VAT_INPUT
    """
    value = _amount_decimal(amount)

    if correction_kind == "decrease":
        lines = (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine(
                role=AccountingAccountRole.VAT_INPUT,
                debit=value,
                credit=ZERO,
            ),
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine(
                role=AccountingAccountRole.TAX_SETTLEMENT,
                debit=ZERO,
                credit=value,
            ),
        )
    elif correction_kind == "increase":
        lines = (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine(
                role=AccountingAccountRole.TAX_SETTLEMENT,
                debit=value,
                credit=ZERO,
            ),
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine(
                role=AccountingAccountRole.VAT_INPUT,
                debit=ZERO,
                credit=value,
            ),
        )
    else:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "correction_kind must be 'decrease' or 'increase'"
            )
        )

    return PurchaseValueCorrectionInputVatCreditCorrectionAccountingPlan(
        lines=lines
    )


def required_roles_for_purchase_value_correction_input_vat_credit_correction_plan(
    plan: PurchaseValueCorrectionInputVatCreditCorrectionAccountingPlan,
) -> tuple[AccountingAccountRole, ...]:
    if not isinstance(
        plan,
        PurchaseValueCorrectionInputVatCreditCorrectionAccountingPlan,
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "Invalid PVC INPUT VAT credit correction accounting plan"
            )
        )

    if not plan.lines:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                "PVC INPUT VAT credit correction accounting plan "
                "must contain lines"
            )
        )

    result = []
    for line in plan.lines:
        if not isinstance(
            line,
            PurchaseValueCorrectionInputVatCreditCorrectionAccountingLine,
        ):
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionAccountingError(
                    "PVC INPUT VAT credit correction accounting plan "
                    "contains invalid line"
                )
            )

        if line.role not in result:
            result.append(line.role)

    return tuple(result)
