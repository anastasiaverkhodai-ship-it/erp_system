from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_return_vat_adjustment_accounting_service import (
    PurchaseReturnVatAdjustmentAccountingError,
    create_purchase_return_vat_adjustment_accounting_plan,
    required_roles_for_purchase_return_vat_adjustment_plan,
)


def test_original_is_dr_631_cr_644():
    plan = (
        create_purchase_return_vat_adjustment_accounting_plan(
            amount=Decimal("20.00")
        )
    )

    assert len(
        plan.lines
    ) == 2

    assert (
        plan.lines[
            0
        ].role
        == AccountingAccountRole.SUPPLIER_PAYABLES
    )
    assert (
        plan.lines[
            0
        ].debit
        == Decimal("20.00")
    )
    assert (
        plan.lines[
            0
        ].credit
        == Decimal("0")
    )

    assert (
        plan.lines[
            1
        ].role
        == AccountingAccountRole.VAT_INPUT
    )
    assert (
        plan.lines[
            1
        ].debit
        == Decimal("0")
    )
    assert (
        plan.lines[
            1
        ].credit
        == Decimal("20.00")
    )


def test_required_roles():
    plan = (
        create_purchase_return_vat_adjustment_accounting_plan(
            amount=Decimal("0.01")
        )
    )

    assert (
        required_roles_for_purchase_return_vat_adjustment_plan(
            plan
        )
        == (
            AccountingAccountRole.SUPPLIER_PAYABLES,
            AccountingAccountRole.VAT_INPUT,
        )
    )


@pytest.mark.parametrize(
    "amount",
    (
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ),
)
def test_invalid_plan_amount_fails(
    amount,
):
    with pytest.raises(
        PurchaseReturnVatAdjustmentAccountingError
    ):
        create_purchase_return_vat_adjustment_accounting_plan(
            amount=amount
        )
