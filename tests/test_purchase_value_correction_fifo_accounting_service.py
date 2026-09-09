from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_value_correction_fifo_accounting_service import (
    PurchaseValueCorrectionFifoAccountingAmountError,
    PurchaseValueCorrectionFifoAccountingDestinationError,
    create_purchase_value_correction_fifo_accounting_plan,
    required_roles_for_purchase_value_correction_fifo_plan,
)


ZERO = Decimal("0")


def line_by_role(
    plan,
    role,
):
    return next(
        line
        for line in plan.lines
        if line.role == role
    )


def destination_line(
    plan,
):
    return next(
        line
        for line in plan.lines
        if line.destination_kind is not None
    )


def test_on_hand_price_increase():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="100.00",
        corrected_base_amount="112.00",
        destination_kind="on_hand",
    )

    inventory = line_by_role(
        plan,
        AccountingAccountRole.INVENTORY_GOODS,
    )

    supplier = line_by_role(
        plan,
        AccountingAccountRole.SUPPLIER_PAYABLES,
    )

    assert plan.delta == Decimal("12.00")
    assert plan.amount == Decimal("12.00")

    assert inventory.debit == Decimal("12.00")
    assert inventory.credit == ZERO

    assert supplier.debit == ZERO
    assert supplier.credit == Decimal("12.00")


def test_on_hand_price_decrease():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="120.00",
        corrected_base_amount="108.00",
        destination_kind="on_hand",
    )

    inventory = line_by_role(
        plan,
        AccountingAccountRole.INVENTORY_GOODS,
    )

    supplier = line_by_role(
        plan,
        AccountingAccountRole.SUPPLIER_PAYABLES,
    )

    assert plan.delta == Decimal("-12.00")

    assert supplier.debit == Decimal("12.00")
    assert supplier.credit == ZERO

    assert inventory.debit == ZERO
    assert inventory.credit == Decimal("12.00")


def test_issued_price_increase_keeps_destination_unresolved():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="50.00",
        corrected_base_amount="55.00",
        destination_kind="issued",
    )

    destination = destination_line(plan)

    supplier = line_by_role(
        plan,
        AccountingAccountRole.SUPPLIER_PAYABLES,
    )

    assert destination.role is None
    assert destination.destination_kind == "issued"
    assert destination.debit == Decimal("5.00")
    assert destination.credit == ZERO

    assert supplier.debit == ZERO
    assert supplier.credit == Decimal("5.00")


def test_issued_price_decrease_keeps_destination_unresolved():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="50.00",
        corrected_base_amount="47.50",
        destination_kind="issued",
    )

    destination = destination_line(plan)

    supplier = line_by_role(
        plan,
        AccountingAccountRole.SUPPLIER_PAYABLES,
    )

    assert supplier.debit == Decimal("2.50")
    assert supplier.credit == ZERO

    assert destination.debit == ZERO
    assert destination.credit == Decimal("2.50")


def test_on_hand_roles():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="10.00",
        corrected_base_amount="11.00",
        destination_kind="on_hand",
    )

    assert (
        required_roles_for_purchase_value_correction_fifo_plan(
            plan
        )
        == (
            AccountingAccountRole.INVENTORY_GOODS,
            AccountingAccountRole.SUPPLIER_PAYABLES,
        )
    )


def test_issued_roles_leave_historical_destination_unresolved():
    plan = create_purchase_value_correction_fifo_accounting_plan(
        original_base_amount="10.00",
        corrected_base_amount="11.00",
        destination_kind="issued",
    )

    assert (
        required_roles_for_purchase_value_correction_fifo_plan(
            plan
        )
        == (
            AccountingAccountRole.SUPPLIER_PAYABLES,
        )
    )


@pytest.mark.parametrize(
    ("original", "corrected"),
    (
        ("-1.00", "1.00"),
        ("1.00", "-1.00"),
    ),
)
def test_negative_base_rejected(
    original,
    corrected,
):
    with pytest.raises(
        PurchaseValueCorrectionFifoAccountingAmountError,
    ):
        create_purchase_value_correction_fifo_accounting_plan(
            original_base_amount=original,
            corrected_base_amount=corrected,
            destination_kind="on_hand",
        )


def test_zero_delta_rejected():
    with pytest.raises(
        PurchaseValueCorrectionFifoAccountingAmountError,
        match="delta cannot be zero",
    ):
        create_purchase_value_correction_fifo_accounting_plan(
            original_base_amount="10.00",
            corrected_base_amount="10.00",
            destination_kind="on_hand",
        )


def test_unknown_destination_rejected():
    with pytest.raises(
        PurchaseValueCorrectionFifoAccountingDestinationError,
    ):
        create_purchase_value_correction_fifo_accounting_plan(
            original_base_amount="10.00",
            corrected_base_amount="11.00",
            destination_kind="unknown",
        )
