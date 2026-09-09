from decimal import Decimal

import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.purchase_value_correction_moving_average_accounting_service import (
    PurchaseValueCorrectionMovingAverageAccountingAmountError,
    PurchaseValueCorrectionMovingAverageAccountingDestinationError,
    create_purchase_value_correction_moving_average_accounting_plan,
    required_roles_for_purchase_value_correction_moving_average_plan,
)


def D(value: str) -> Decimal:
    return Decimal(
        value
    )


def test_on_hand_decrease():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("600"),
            corrected_valuation_amount=D("540"),
            destination_kind="on_hand",
        )
    )

    assert plan.delta == D("-60")
    assert plan.amount == D("60")

    destination, supplier = plan.lines

    assert destination.role == AccountingAccountRole.INVENTORY_GOODS
    assert destination.debit == D("0")
    assert destination.credit == D("60")

    assert supplier.role == AccountingAccountRole.SUPPLIER_PAYABLES
    assert supplier.debit == D("60")
    assert supplier.credit == D("0")


def test_on_hand_increase():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("540"),
            corrected_valuation_amount=D("600"),
            destination_kind="on_hand",
        )
    )

    destination, supplier = plan.lines

    assert destination.debit == D("60")
    assert destination.credit == D("0")
    assert supplier.debit == D("0")
    assert supplier.credit == D("60")


def test_issued_decrease_defers_historical_destination():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("400"),
            corrected_valuation_amount=D("360"),
            destination_kind="issued",
        )
    )

    destination, supplier = plan.lines

    assert destination.role is None
    assert destination.destination_kind == "issued"
    assert destination.debit == D("0")
    assert destination.credit == D("40")

    assert supplier.role == AccountingAccountRole.SUPPLIER_PAYABLES
    assert supplier.debit == D("40")
    assert supplier.credit == D("0")


def test_issued_increase_defers_historical_destination():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("360"),
            corrected_valuation_amount=D("400"),
            destination_kind="issued",
        )
    )

    destination, supplier = plan.lines

    assert destination.role is None
    assert destination.debit == D("40")
    assert destination.credit == D("0")
    assert supplier.credit == D("40")


def test_required_roles_on_hand():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("100"),
            corrected_valuation_amount=D("90"),
            destination_kind="on_hand",
        )
    )

    assert (
        required_roles_for_purchase_value_correction_moving_average_plan(
            plan
        )
        == (
            AccountingAccountRole.INVENTORY_GOODS,
            AccountingAccountRole.SUPPLIER_PAYABLES,
        )
    )


def test_required_roles_issued_excludes_current_cogs_role():
    plan = (
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("100"),
            corrected_valuation_amount=D("90"),
            destination_kind="issued",
        )
    )

    assert (
        required_roles_for_purchase_value_correction_moving_average_plan(
            plan
        )
        == (
            AccountingAccountRole.SUPPLIER_PAYABLES,
        )
    )


@pytest.mark.parametrize(
    "original,corrected",
    [
        ("-1", "1"),
        ("1", "-1"),
    ],
)
def test_negative_amount_fails(
    original,
    corrected,
):
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageAccountingAmountError
    ):
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D(original),
            corrected_valuation_amount=D(corrected),
            destination_kind="on_hand",
        )


def test_zero_delta_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageAccountingAmountError
    ):
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("100"),
            corrected_valuation_amount=D("100"),
            destination_kind="on_hand",
        )


def test_invalid_destination_fails():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageAccountingDestinationError
    ):
        create_purchase_value_correction_moving_average_accounting_plan(
            original_valuation_amount=D("100"),
            corrected_valuation_amount=D("90"),
            destination_kind="something_else",
        )
