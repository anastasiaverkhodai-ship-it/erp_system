from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.input_vat_accounting_service import (
    InputVatAccountingAmountError,
    create_input_vat_fulfillment_bridge_accounting_plan,
    create_input_vat_recognition_accounting_plan,
    required_roles_for_input_vat_plan,
)


ZERO = Decimal("0.00")


def assert_balanced(
    plan,
) -> None:
    total_debit = sum(
        (
            line.debit
            for line in plan.lines
        ),
        ZERO,
    )

    total_credit = sum(
        (
            line.credit
            for line in plan.lines
        ),
        ZERO,
    )

    assert (
        total_debit
        == total_credit
    )


def test_fulfillment_bridge_posts_dr_644_cr_631_roles():
    plan = (
        create_input_vat_fulfillment_bridge_accounting_plan(
            amount=Decimal("20.00")
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit_line = plan.lines[0]
    credit_line = plan.lines[1]

    assert (
        debit_line.role
        == AccountingAccountRole.VAT_INPUT
    )

    assert (
        debit_line.debit
        == Decimal("20.00")
    )

    assert (
        debit_line.credit
        == ZERO
    )

    assert (
        credit_line.role
        == AccountingAccountRole.SUPPLIER_PAYABLES
    )

    assert (
        credit_line.debit
        == ZERO
    )

    assert (
        credit_line.credit
        == Decimal("20.00")
    )

    assert_balanced(
        plan
    )


def test_recognition_posts_dr_641_cr_644_roles():
    plan = (
        create_input_vat_recognition_accounting_plan(
            amount=Decimal("20.00")
        )
    )

    assert len(
        plan.lines
    ) == 2

    debit_line = plan.lines[0]
    credit_line = plan.lines[1]

    assert (
        debit_line.role
        == AccountingAccountRole.TAX_SETTLEMENT
    )

    assert (
        debit_line.debit
        == Decimal("20.00")
    )

    assert (
        debit_line.credit
        == ZERO
    )

    assert (
        credit_line.role
        == AccountingAccountRole.VAT_INPUT
    )

    assert (
        credit_line.debit
        == ZERO
    )

    assert (
        credit_line.credit
        == Decimal("20.00")
    )

    assert_balanced(
        plan
    )


def test_input_vat_amount_uses_money_rounding():
    bridge = (
        create_input_vat_fulfillment_bridge_accounting_plan(
            amount=Decimal("20.005")
        )
    )

    recognition = (
        create_input_vat_recognition_accounting_plan(
            amount=Decimal("20.005")
        )
    )

    assert (
        bridge.lines[0].debit
        == Decimal("20.01")
    )

    assert (
        bridge.lines[1].credit
        == Decimal("20.01")
    )

    assert (
        recognition.lines[0].debit
        == Decimal("20.01")
    )

    assert (
        recognition.lines[1].credit
        == Decimal("20.01")
    )


@pytest.mark.parametrize(
    "builder",
    [
        create_input_vat_fulfillment_bridge_accounting_plan,
        create_input_vat_recognition_accounting_plan,
    ],
)
@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.00"),
        Decimal("-0.01"),
    ],
)
def test_input_vat_plans_reject_non_positive_amount(
    builder,
    amount,
):
    with pytest.raises(
        InputVatAccountingAmountError,
        match=(
            "INPUT VAT journal amount "
            "must be greater than zero"
        ),
    ):
        builder(
            amount=amount
        )


def test_fulfillment_bridge_required_roles():
    plan = (
        create_input_vat_fulfillment_bridge_accounting_plan(
            amount=Decimal("10.00")
        )
    )

    assert (
        required_roles_for_input_vat_plan(
            plan
        )
        == (
            AccountingAccountRole.VAT_INPUT,
            AccountingAccountRole.SUPPLIER_PAYABLES,
        )
    )


def test_recognition_required_roles():
    plan = (
        create_input_vat_recognition_accounting_plan(
            amount=Decimal("10.00")
        )
    )

    assert (
        required_roles_for_input_vat_plan(
            plan
        )
        == (
            AccountingAccountRole.TAX_SETTLEMENT,
            AccountingAccountRole.VAT_INPUT,
        )
    )


def test_required_roles_rejects_wrong_plan_type():
    with pytest.raises(
        TypeError,
        match=(
            "plan must be "
            "TaxRecognitionAccountingPlan"
        ),
    ):
        required_roles_for_input_vat_plan(
            object()
        )
