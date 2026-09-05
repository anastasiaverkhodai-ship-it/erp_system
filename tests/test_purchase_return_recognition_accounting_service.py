from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_return_recognition_accounting_service import (
    PurchaseReturnRecognitionAccountingError,
    create_purchase_return_recognition_accounting_plan,
    required_roles_for_purchase_return_recognition_plan,
)


def test_purchase_return_plan_is_dr631_cr281():
    plan = (
        create_purchase_return_recognition_accounting_plan(
            amount=Decimal("12.34"),
        )
    )

    assert len(plan.lines) == 2

    debit = plan.lines[0]
    credit = plan.lines[1]

    assert (
        debit.role
        == AccountingAccountRole.SUPPLIER_PAYABLES
    )
    assert debit.debit == Decimal("12.34")
    assert debit.credit == Decimal("0")

    assert (
        credit.role
        == AccountingAccountRole.INVENTORY_GOODS
    )
    assert credit.debit == Decimal("0")
    assert credit.credit == Decimal("12.34")


def test_required_roles_are_payables_then_inventory():
    plan = (
        create_purchase_return_recognition_accounting_plan(
            amount=Decimal("1.00"),
        )
    )

    assert (
        required_roles_for_purchase_return_recognition_plan(
            plan
        )
        == (
            AccountingAccountRole.SUPPLIER_PAYABLES,
            AccountingAccountRole.INVENTORY_GOODS,
        )
    )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("Infinity"),
        "bad",
    ],
)
def test_accounting_plan_rejects_non_positive_or_invalid_amount(
    amount,
):
    with pytest.raises(
        PurchaseReturnRecognitionAccountingError
    ):
        create_purchase_return_recognition_accounting_plan(
            amount=amount,
        )


def test_plan_uses_only_economic_base_amount():
    plan = (
        create_purchase_return_recognition_accounting_plan(
            amount=Decimal("0.02"),
        )
    )

    assert plan.lines[0].debit == Decimal("0.02")
    assert plan.lines[1].credit == Decimal("0.02")
