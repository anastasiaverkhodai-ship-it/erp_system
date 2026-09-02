from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.vat_advance_bridge_accounting_service import (
    VatAdvanceBridgeAccountingError,
    VatAdvanceBridgeAccountingPlan,
    create_vat_advance_bridge_accounting_plan,
    required_roles_for_vat_advance_bridge_plan,
)


def test_bridge_accounting_plan_posts_dr_702_cr_643_roles():
    amount = Decimal("20.00")

    plan = (
        create_vat_advance_bridge_accounting_plan(
            amount=amount,
        )
    )

    assert isinstance(
        plan,
        VatAdvanceBridgeAccountingPlan,
    )

    assert plan.amount == amount

    assert len(
        plan.lines
    ) == 2

    debit_line = plan.lines[0]
    credit_line = plan.lines[1]

    assert (
        debit_line.role
        == AccountingAccountRole.GOODS_REVENUE
    )

    assert debit_line.debit == amount
    assert debit_line.credit == Decimal("0")

    assert (
        credit_line.role
        == AccountingAccountRole.VAT_OUTPUT
    )

    assert credit_line.debit == Decimal("0")
    assert credit_line.credit == amount


def test_partial_bridge_accounting_plan_preserves_exact_amount():
    amount = Decimal("10.00")

    plan = (
        create_vat_advance_bridge_accounting_plan(
            amount=amount,
        )
    )

    assert plan.amount == Decimal("10.00")

    assert (
        plan.lines[0].debit
        == Decimal("10.00")
    )

    assert (
        plan.lines[1].credit
        == Decimal("10.00")
    )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-0.01"),
        Decimal("-20.00"),
    ],
)
def test_non_positive_bridge_amount_is_rejected(
    amount,
):
    with pytest.raises(
        VatAdvanceBridgeAccountingError,
        match="must be greater than zero",
    ):
        create_vat_advance_bridge_accounting_plan(
            amount=amount,
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_non_finite_bridge_amount_is_rejected(
    amount,
):
    with pytest.raises(
        VatAdvanceBridgeAccountingError,
        match="must be finite",
    ):
        create_vat_advance_bridge_accounting_plan(
            amount=amount,
        )


@pytest.mark.parametrize(
    "amount",
    [
        "20.00",
        20,
        20.0,
        None,
    ],
)
def test_non_decimal_bridge_amount_is_rejected(
    amount,
):
    with pytest.raises(
        VatAdvanceBridgeAccountingError,
        match="must be Decimal",
    ):
        create_vat_advance_bridge_accounting_plan(
            amount=amount,
        )


def test_required_roles_are_goods_revenue_then_vat_output():
    plan = (
        create_vat_advance_bridge_accounting_plan(
            amount=Decimal("20.00"),
        )
    )

    assert (
        required_roles_for_vat_advance_bridge_plan(
            plan
        )
        == (
            AccountingAccountRole
            .GOODS_REVENUE,
            AccountingAccountRole
            .VAT_OUTPUT,
        )
    )


def test_required_roles_reject_invalid_plan_type():
    with pytest.raises(
        VatAdvanceBridgeAccountingError,
        match="invalid type",
    ):
        required_roles_for_vat_advance_bridge_plan(
            object()
        )
