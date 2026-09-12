from datetime import date
from decimal import Decimal

import pytest

from app.services.inventory_count_history_service import (
    InventoryCountHistoryAction,
    InventoryCountHistoryError,
    InventoryCountHistoryItem,
    normalize_history_key,
    normalize_inventory_count_target,
    plan_inventory_count_history,
    resolve_active_inventory_count_original,
)


KEY_A = "33333333-3333-4333-8333-333333333333"
KEY_B = "44444444-4444-4444-8444-444444444444"

D1 = date(2026, 9, 10)
D2 = date(2026, 9, 11)


def target(
    *,
    expected: str = "10",
    counted: str = "8",
    count_date: date = D1,
):
    return normalize_inventory_count_target(
        company_id=1,
        product_id=100,
        warehouse_id=10,
        count_date=count_date,
        expected_quantity=Decimal(expected),
        counted_quantity=Decimal(counted),
    )


def original(
    *,
    row_id: int = 1,
    history_key: str = KEY_A,
    value=None,
):
    return InventoryCountHistoryItem(
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
    return InventoryCountHistoryItem(
        id=row_id,
        history_key=history_key,
        target=None,
        reversal_of_id=reversal_of_id,
    )


def test_history_key_requires_canonical_uuid():
    assert normalize_history_key(KEY_A) == KEY_A

    with pytest.raises(
        InventoryCountHistoryError,
        match="valid UUID",
    ):
        normalize_history_key("abc")


def test_count_target_snapshots_variance():
    value = target(
        expected="10",
        counted="12.5",
    )

    assert value.variance_quantity == Decimal("2.5")


def test_count_target_allows_zero_quantities():
    value = target(
        expected="0",
        counted="0",
    )

    assert value.variance_quantity == Decimal("0")


def test_count_target_rejects_negative_quantity():
    with pytest.raises(
        InventoryCountHistoryError,
        match="cannot be negative",
    ):
        target(expected="-1")


def test_no_history_no_target_is_noop():
    plan = plan_inventory_count_history(
        history=(),
        history_key=KEY_A,
        target=None,
    )

    assert plan.action == InventoryCountHistoryAction.NOOP


def test_no_history_target_creates_original():
    plan = plan_inventory_count_history(
        history=(),
        history_key=KEY_A,
        target=target(),
    )

    assert plan.action == InventoryCountHistoryAction.CREATE
    assert plan.history_key == KEY_A


def test_identical_target_is_noop():
    value = target()

    plan = plan_inventory_count_history(
        history=(original(value=value),),
        history_key=KEY_A,
        target=value,
    )

    assert plan.action == InventoryCountHistoryAction.NOOP


def test_removed_count_reverses_active_original():
    plan = plan_inventory_count_history(
        history=(original(),),
        history_key=KEY_A,
        target=None,
        adjustment_date=D2,
    )

    assert plan.action == InventoryCountHistoryAction.REVERSE


def test_changed_count_reverses_and_replaces():
    plan = plan_inventory_count_history(
        history=(original(),),
        history_key=KEY_A,
        target=target(
            counted="9",
            count_date=D2,
        ),
        adjustment_date=D2,
    )

    assert (
        plan.action
        == InventoryCountHistoryAction.REVERSE_AND_REPLACE
    )


def test_reversal_removes_active_inside_same_chain():
    assert (
        resolve_active_inventory_count_original(
            (
                original(),
                reversal(),
            ),
            history_key=KEY_A,
        )
        is None
    )


def test_replacement_is_active_inside_same_chain():
    replacement = original(
        row_id=3,
        value=target(
            counted="9",
            count_date=D2,
        ),
    )

    active = resolve_active_inventory_count_original(
        (
            original(),
            reversal(),
            replacement,
        ),
        history_key=KEY_A,
    )

    assert active == replacement


def test_independent_active_counts_are_valid():
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

    active_a = resolve_active_inventory_count_original(
        history,
        history_key=KEY_A,
    )
    active_b = resolve_active_inventory_count_original(
        history,
        history_key=KEY_B,
    )

    assert active_a.id == 1
    assert active_b.id == 2


def test_unknown_reversal_inside_chain_is_rejected():
    with pytest.raises(
        InventoryCountHistoryError,
        match="unknown original",
    ):
        resolve_active_inventory_count_original(
            (
                InventoryCountHistoryItem(
                    id=2,
                    history_key=KEY_A,
                    target=target(),
                    reversal_of_id=999,
                ),
            ),
            history_key=KEY_A,
        )


def test_multiple_active_same_chain_rejected():
    with pytest.raises(
        InventoryCountHistoryError,
        match="multiple ACTIVE",
    ):
        resolve_active_inventory_count_original(
            (
                original(row_id=1),
                original(row_id=2),
            ),
            history_key=KEY_A,
        )


def test_replacement_cannot_move_backward_in_time():
    later = original(
        value=target(
            count_date=D2,
        )
    )

    with pytest.raises(
        InventoryCountHistoryError,
        match="backward in time",
    ):
        plan_inventory_count_history(
            history=(later,),
            history_key=KEY_A,
            target=target(
                count_date=D1,
            ),
            adjustment_date=D2,
        )


def test_original_without_target_is_rejected():
    with pytest.raises(
        InventoryCountHistoryError,
        match="requires a target",
    ):
        resolve_active_inventory_count_original(
            (
                InventoryCountHistoryItem(
                    id=99,
                    history_key=KEY_A,
                    target=None,
                    reversal_of_id=None,
                ),
            ),
            history_key=KEY_A,
        )
