from decimal import Decimal

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.tax_recognition_accounting_service import (
    TaxRecognitionAccountingError,
    TaxRecognitionAccountingLinePlan,
    TaxRecognitionAccountingPlan,
    money,
)


ZERO = Decimal("0.00")


class InputVatAccountingError(
    TaxRecognitionAccountingError
):
    """Base error for INPUT VAT accounting plans."""


class InputVatAccountingAmountError(
    InputVatAccountingError
):
    """INPUT VAT accounting amount is invalid."""


def _positive_input_vat_money(
    amount: Decimal,
) -> Decimal:
    normalized = money(
        amount
    )

    if normalized <= ZERO:
        raise InputVatAccountingAmountError(
            "INPUT VAT journal amount "
            "must be greater than zero"
        )

    return normalized


def create_input_vat_fulfillment_bridge_accounting_plan(
    *,
    amount: Decimal,
) -> TaxRecognitionAccountingPlan:
    """
    Build the economic INPUT VAT leg for received purchases.

    This is NOT legal tax-credit recognition.

    When purchase goods/services are economically received, the
    recoverable VAT component is kept on VAT_INPUT (GENERAL 291:
    account 644) while the supplier liability becomes gross:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

    Later qualifying TaxCreditEvidence recognition transfers the
    amount from VAT_INPUT to TAX_SETTLEMENT.

    The fulfillment bridge may therefore exist before legal tax
    credit recognition, after it, or in the same transaction
    lifecycle depending on business-event chronology.
    """

    amount = _positive_input_vat_money(
        amount
    )

    return TaxRecognitionAccountingPlan(
        lines=(
            TaxRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .VAT_INPUT
                ),
                debit=amount,
                credit=ZERO,
            ),
            TaxRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .SUPPLIER_PAYABLES
                ),
                debit=ZERO,
                credit=amount,
            ),
        )
    )


def create_input_vat_recognition_accounting_plan(
    *,
    amount: Decimal,
) -> TaxRecognitionAccountingPlan:
    """
    Build the legal INPUT VAT tax-credit recognition leg.

    A qualifying evidence-gated INPUT TaxRecognitionEvent transfers
    the recognized amount from interim VAT_INPUT to VAT settlement:

        Dr TAX_SETTLEMENT
        Cr VAT_INPUT

    GENERAL 291 working-profile mapping:

        TAX_SETTLEMENT -> 641
        VAT_INPUT      -> 644
    """

    amount = _positive_input_vat_money(
        amount
    )

    return TaxRecognitionAccountingPlan(
        lines=(
            TaxRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .TAX_SETTLEMENT
                ),
                debit=amount,
                credit=ZERO,
            ),
            TaxRecognitionAccountingLinePlan(
                role=(
                    AccountingAccountRole
                    .VAT_INPUT
                ),
                debit=ZERO,
                credit=amount,
            ),
        )
    )


def required_roles_for_input_vat_plan(
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
            for line in plan.lines
        )
    )
