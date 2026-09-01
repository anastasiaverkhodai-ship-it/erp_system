from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.sales_recognition_accounting_service import (
    SalesRecognitionAccountingAmountError,
    create_sales_recognition_accounting_plan,
    required_roles_for_sales_recognition_plan,
)


ZERO = Decimal("0.00")


def test_gross_sales_recognition_plan():

    plan = (
        create_sales_recognition_accounting_plan(
            amount=Decimal("120.00"),
        )
    )

    assert len(plan.lines) == 2

    receivable = plan.lines[0]
    revenue = plan.lines[1]

    assert (
        receivable.role
        == AccountingAccountRole.CUSTOMER_RECEIVABLES
    )
    assert (
        receivable.debit
        == Decimal("120.00")
    )
    assert receivable.credit == ZERO

    assert (
        revenue.role
        == AccountingAccountRole.GOODS_REVENUE
    )
    assert revenue.debit == ZERO
    assert (
        revenue.credit
        == Decimal("120.00")
    )


def test_plan_is_balanced():

    plan = (
        create_sales_recognition_accounting_plan(
            amount=Decimal("120.00"),
        )
    )

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

    assert total_debit == Decimal("120.00")
    assert total_credit == Decimal("120.00")
    assert total_debit == total_credit


def test_amount_is_rounded_to_money_quantum():

    plan = (
        create_sales_recognition_accounting_plan(
            amount=Decimal("120.005"),
        )
    )

    assert (
        plan.lines[0].debit
        == Decimal("120.01")
    )
    assert (
        plan.lines[1].credit
        == Decimal("120.01")
    )


@pytest.mark.parametrize(
    "amount",
    (
        Decimal("0.00"),
        Decimal("-0.01"),
        Decimal("-100.00"),
    ),
)
def test_non_positive_amount_is_rejected(
    amount,
):

    with pytest.raises(
        SalesRecognitionAccountingAmountError
    ):
        create_sales_recognition_accounting_plan(
            amount=amount,
        )


def test_required_roles():

    plan = (
        create_sales_recognition_accounting_plan(
            amount=Decimal("120.00"),
        )
    )

    assert (
        required_roles_for_sales_recognition_plan(
            plan
        )
        == (
            AccountingAccountRole.CUSTOMER_RECEIVABLES,
            AccountingAccountRole.GOODS_REVENUE,
        )
    )


def test_required_roles_rejects_wrong_type():

    with pytest.raises(
        TypeError
    ):
        required_roles_for_sales_recognition_plan(
            object()
        )


def test_plan_is_immutable():

    plan = (
        create_sales_recognition_accounting_plan(
            amount=Decimal("120.00"),
        )
    )

    with pytest.raises(
        FrozenInstanceError
    ):
        plan.lines = ()
