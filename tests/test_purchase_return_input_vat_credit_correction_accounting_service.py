from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_return_input_vat_credit_correction_accounting_service import (
    PurchaseReturnInputVatCreditCorrectionAccountingError,
    create_purchase_return_input_vat_credit_correction_accounting_plan,
    required_roles_for_purchase_return_input_vat_credit_correction_plan,
)


def test_original_posts_dr_644_cr_641_roles():
    plan = (
        create_purchase_return_input_vat_credit_correction_accounting_plan(
            amount=Decimal(
                "5.00"
            )
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit, credit = plan.lines

    assert (
        debit.role
        == AccountingAccountRole.VAT_INPUT
    )

    assert (
        debit.debit
        == Decimal(
            "5.00"
        )
    )

    assert (
        debit.credit
        == Decimal(
            "0"
        )
    )

    assert (
        credit.role
        == AccountingAccountRole.TAX_SETTLEMENT
    )

    assert (
        credit.debit
        == Decimal(
            "0"
        )
    )

    assert (
        credit.credit
        == Decimal(
            "5.00"
        )
    )


def test_required_roles_are_644_then_641_roles():
    plan = (
        create_purchase_return_input_vat_credit_correction_accounting_plan(
            amount=Decimal(
                "3.00"
            )
        )
    )

    assert (
        required_roles_for_purchase_return_input_vat_credit_correction_plan(
            plan
        )
        == (
            AccountingAccountRole.VAT_INPUT,
            AccountingAccountRole.TAX_SETTLEMENT,
        )
    )


@pytest.mark.parametrize(
    "amount",
    (
        Decimal(
            "0"
        ),
        Decimal(
            "-0.01"
        ),
        Decimal(
            "NaN"
        ),
        Decimal(
            "Infinity"
        ),
        "not-a-decimal",
    ),
)
def test_invalid_accounting_amount_is_rejected(
    amount,
):
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionAccountingError
    ):
        create_purchase_return_input_vat_credit_correction_accounting_plan(
            amount=amount
        )
