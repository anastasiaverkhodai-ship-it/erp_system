from datetime import date
from decimal import Decimal

import pytest

from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryAction,
    WarehouseTransferHistoryError,
    WarehouseTransferHistoryItem,
    WarehouseTransferLineTarget,
    normalize_history_key,
    normalize_transfer_target,
    plan_warehouse_transfer_history,
    resolve_active_transfer_original,
)


KEY_A = "11111111-1111-4111-8111-111111111111"
KEY_B = "22222222-2222-4222-8222-222222222222"

D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


def target(
    *,
    quantity: str = "5",
    transfer_date: date = D1,
):
    return normalize_transfer_target(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=transfer_date,
        lines=(
            WarehouseTransferLineTarget(
                product_id=100,
                quantity=Decimal(quantity),
            ),
        ),
    )


def original(
    *,
    row_id: int = 1,
    history_key: str = KEY_A,
    value=None,
):
    return WarehouseTransferHistoryItem(
        id=row_id,
        history_key=history_key,
        target=value or target(),
        reversal_of_id=None,
    )


def reversal(
    *,
    row_id: int = 2,
    history_key: str = KEY_A,
    reversal_of_id: int = 1,
):
    return WarehouseTransferHistoryItem(
        id=row_id,
        history_key=history_key,
        target=None,
        reversal_of_id=reversal_of_id,
    )


def test_history_key_requires_canonical_uuid():
    assert normalize_history_key(KEY_A) == KEY_A

    with pytest.raises(
        WarehouseTransferHistoryError,
        match="valid UUID",
    ):
        normalize_history_key("abc")


def test_transfer_target_canonicalizes_line_order():
    value = normalize_transfer_target(
        company_id=1,
        source_warehouse_id=10,
        destination_warehouse_id=20,
        transfer_date=D1,
        lines=(
            WarehouseTransferLineTarget(
                product_id=200,
                quantity=Decimal("2"),
            ),
            WarehouseTransferLineTarget(
                product_id=100,
                quantity=Decimal("1"),
            ),
        ),
    )

    assert [
        line.product_id
        for line in value.lines
    ] == [100, 200]


def test_transfer_target_rejects_duplicate_product():
    with pytest.raises(
        WarehouseTransferHistoryError,
        match="duplicate product_id",
    ):
        normalize_transfer_target(
            company_id=1,
            source_warehouse_id=10,
            destination_warehouse_id=20,
            transfer_date=D1,
            lines=(
                WarehouseTransferLineTarget(
                    product_id=100,
                    quantity=Decimal("1"),
                ),
                WarehouseTransferLineTarget(
                    product_id=100,
                    quantity=Decimal("2"),
                ),
            ),
        )


def test_no_history_no_target_is_noop():
    plan = plan_warehouse_transfer_history(
        history=(),
        history_key=KEY_A,
        target=None,
    )

    assert plan.action == WarehouseTransferHistoryAction.NOOP


def test_no_history_target_creates_original():
    plan = plan_warehouse_transfer_history(
        history=(),
        history_key=KEY_A,
        target=target(),
    )

    assert plan.action == WarehouseTransferHistoryAction.CREATE
    assert plan.history_key == KEY_A


def test_identical_target_is_noop():
    value = target()

    plan = plan_warehouse_transfer_history(
        history=(
            original(value=value),
        ),
        history_key=KEY_A,
        target=value,
    )

    assert plan.action == WarehouseTransferHistoryAction.NOOP


def test_removed_target_reverses_active_original():
    plan = plan_warehouse_transfer_history(
        history=(original(),),
        history_key=KEY_A,
        target=None,
        adjustment_date=D2,
    )

    assert plan.action == WarehouseTransferHistoryAction.REVERSE
    assert plan.active_original.id == 1


def test_changed_target_reverses_and_replaces():
    plan = plan_warehouse_transfer_history(
        history=(original(),),
        history_key=KEY_A,
        target=target(
            quantity="7",
            transfer_date=D2,
        ),
        adjustment_date=D2,
    )

    assert (
        plan.action
        == WarehouseTransferHistoryAction.REVERSE_AND_REPLACE
    )


def test_reversal_removes_active_inside_same_chain():
    assert (
        resolve_active_transfer_original(
            (
                original(),
                reversal(),
            ),
            history_key=KEY_A,
        )
        is None
    )


def test_replacement_becomes_active_inside_same_chain():
    replacement = original(
        row_id=3,
        value=target(
            quantity="7",
            transfer_date=D2,
        ),
    )

    active = resolve_active_transfer_original(
        (
            original(),
            reversal(),
            replacement,
        ),
        history_key=KEY_A,
    )

    assert active == replacement


def test_independent_active_transfers_are_valid():
    history = (
        original(
            row_id=1,
            history_key=KEY_A,
        ),
        original(
            row_id=2,
            history_key=KEY_B,
        ),
    )

    active_a = resolve_active_transfer_original(
        history,
        history_key=KEY_A,
    )
    active_b = resolve_active_transfer_original(
        history,
        history_key=KEY_B,
    )

    assert active_a.id == 1
    assert active_b.id == 2


def test_duplicate_reversal_inside_chain_is_rejected():
    with pytest.raises(
        WarehouseTransferHistoryError,
        match="reversed more than once",
    ):
        resolve_active_transfer_original(
            (
                original(),
                reversal(row_id=2),
                reversal(row_id=3),
            ),
            history_key=KEY_A,
        )


def test_multiple_active_originals_same_chain_rejected():
    with pytest.raises(
        WarehouseTransferHistoryError,
        match="multiple ACTIVE",
    ):
        resolve_active_transfer_original(
            (
                original(row_id=1),
                original(row_id=2),
            ),
            history_key=KEY_A,
        )


def test_replacement_cannot_move_backward_in_time():
    later = original(
        value=target(
            transfer_date=D2,
        )
    )

    with pytest.raises(
        WarehouseTransferHistoryError,
        match="backward in time",
    ):
        plan_warehouse_transfer_history(
            history=(later,),
            history_key=KEY_A,
            target=target(
                transfer_date=D1,
            ),
            adjustment_date=D2,
        )


def test_original_without_target_is_rejected():
    with pytest.raises(
        WarehouseTransferHistoryError,
        match="requires a target",
    ):
        resolve_active_transfer_original(
            (
                WarehouseTransferHistoryItem(
                    id=99,
                    history_key=KEY_A,
                    target=None,
                    reversal_of_id=None,
                ),
            ),
            history_key=KEY_A,
        )
