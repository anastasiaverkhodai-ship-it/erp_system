from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_return_recognition_accounting_service import (
    SalesReturnRecognitionAccountingAmountError,
    create_sales_return_recognition_accounting_plan,
    required_roles_for_sales_return_recognition_plan,
)


def test_sales_return_posts_gross_704_361_plan():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("120.00")
        )
    )

    assert len(
        plan.lines
    ) == 2

    assert (
        plan.lines[0].role
        == AccountingAccountRole.SALES_DEDUCTIONS
    )

    assert (
        plan.lines[0].debit
        == Decimal("120.00")
    )

    assert (
        plan.lines[0].credit
        == Decimal("0.00")
    )

    assert (
        plan.lines[1].role
        == AccountingAccountRole.CUSTOMER_RECEIVABLES
    )

    assert (
        plan.lines[1].debit
        == Decimal("0.00")
    )

    assert (
        plan.lines[1].credit
        == Decimal("120.00")
    )


def test_partial_return_uses_returned_gross_amount():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("60")
        )
    )

    assert (
        plan.lines[0].debit
        == Decimal("60.00")
    )

    assert (
        plan.lines[1].credit
        == Decimal("60.00")
    )


def test_rounds_to_money_precision():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("10.005")
        )
    )

    assert (
        plan.lines[0].debit
        == Decimal("10.01")
    )

    assert (
        plan.lines[1].credit
        == Decimal("10.01")
    )


@pytest.mark.parametrize(
    "amount",
    (
        Decimal("0"),
        Decimal("-1"),
        Decimal("-0.001"),
    ),
)
def test_nonpositive_amount_rejected(
    amount,
):
    with pytest.raises(
        SalesReturnRecognitionAccountingAmountError
    ):
        create_sales_return_recognition_accounting_plan(
            amount=amount
        )


def test_required_roles_are_704_then_361():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("120")
        )
    )

    assert (
        required_roles_for_sales_return_recognition_plan(
            plan
        )
        == (
            AccountingAccountRole.SALES_DEDUCTIONS,
            AccountingAccountRole.CUSTOMER_RECEIVABLES,
        )
    )


def test_plan_contains_no_vat_role():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("120")
        )
    )

    roles = {
        line.role
        for line in plan.lines
    }

    assert (
        AccountingAccountRole.VAT_OUTPUT
        not in roles
    )


def test_plan_is_balanced():
    plan = (
        create_sales_return_recognition_accounting_plan(
            amount=Decimal("120")
        )
    )

    total_debit = sum(
        (
            line.debit
            for line in plan.lines
        ),
        Decimal("0"),
    )

    total_credit = sum(
        (
            line.credit
            for line in plan.lines
        ),
        Decimal("0"),
    )

    assert (
        total_debit
        == total_credit
        == Decimal("120.00")
    )
