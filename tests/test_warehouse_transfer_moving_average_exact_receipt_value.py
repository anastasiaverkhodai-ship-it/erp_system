from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.moving_average_inventory as ma


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(
            value
        )


def balance(
    *,
    quantity="0",
    inventory_value="0",
    average_unit_cost="0",
):
    return SimpleNamespace(
        quantity=Decimal(quantity),
        inventory_value=Decimal(
            inventory_value
        ),
        average_unit_cost=Decimal(
            average_unit_cost
        ),
        updated_at=None,
    )


async def run_receipt(
    monkeypatch,
    *,
    current_balance,
    quantity,
    unit_cost,
    exact_valuation_amount=None,
):
    async def locked_balance(
        **kwargs,
    ):
        return current_balance

    monkeypatch.setattr(
        ma,
        "get_locked_moving_average_balance",
        locked_balance,
    )

    db = FakeDb()

    kwargs = dict(
        db=db,
        company_id=1,
        document_id=10,
        document_line_id=11,
        product_id=20,
        warehouse_id=30,
        movement_date=date(
            2026,
            9,
            10,
        ),
        quantity=Decimal(
            quantity
        ),
        unit_cost=Decimal(
            unit_cost
        ),
    )

    if exact_valuation_amount is not None:
        kwargs[
            "exact_valuation_amount"
        ] = Decimal(
            exact_valuation_amount
        )

    result = await ma.process_moving_average_receipt(
        **kwargs
    )

    assert result is current_balance
    assert len(db.added) == 1

    return (
        result,
        db.added[0],
    )


@pytest.mark.asyncio
async def test_legacy_receipt_still_uses_quantity_times_unit_cost(
    monkeypatch,
):
    current = balance()

    result, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="3",
        unit_cost="3.3333",
    )

    assert (
        movement.value_delta
        == Decimal("9.99990000")
    )

    assert (
        result.inventory_value
        == Decimal("9.99990000")
    )

    assert (
        movement.unit_cost
        == Decimal("3.33330000")
    )


@pytest.mark.asyncio
async def test_exact_transfer_receipt_preserves_source_ice_value(
    monkeypatch,
):
    current = balance()

    result, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="3",
        unit_cost="3.3333",
        exact_valuation_amount="10.00000000",
    )

    assert (
        movement.value_delta
        == Decimal("10.00000000")
    )

    assert (
        result.inventory_value
        == Decimal("10.00000000")
    )

    assert (
        movement.unit_cost
        == Decimal("3.33330000")
    )

    assert (
        result.average_unit_cost
        == Decimal("3.33333333")
    )


@pytest.mark.asyncio
async def test_exact_value_is_added_to_existing_inventory_value(
    monkeypatch,
):
    current = balance(
        quantity="2",
        inventory_value="5",
        average_unit_cost="2.5",
    )

    result, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="3",
        unit_cost="3.3333",
        exact_valuation_amount="10",
    )

    assert (
        result.quantity
        == Decimal("5")
    )

    assert (
        movement.value_delta
        == Decimal("10.00000000")
    )

    assert (
        result.inventory_value
        == Decimal("15.00000000")
    )

    assert (
        result.average_unit_cost
        == Decimal("3.00000000")
    )

    assert (
        movement.balance_quantity_after
        == Decimal("5")
    )

    assert (
        movement.balance_value_after
        == Decimal("15.00000000")
    )

    assert (
        movement.average_unit_cost_after
        == Decimal("3.00000000")
    )


@pytest.mark.asyncio
async def test_exact_value_is_quantized_to_q8(
    monkeypatch,
):
    current = balance()

    _, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="1",
        unit_cost="1",
        exact_valuation_amount="1.123456789",
    )

    assert (
        movement.value_delta
        == Decimal("1.12345679")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    (
        "-0.00000001",
        "-1",
    ),
)
async def test_negative_exact_value_rejected_before_balance_lock(
    monkeypatch,
    value,
):
    called = False

    async def locked_balance(
        **kwargs,
    ):
        nonlocal called
        called = True
        return balance()

    monkeypatch.setattr(
        ma,
        "get_locked_moving_average_balance",
        locked_balance,
    )

    db = FakeDb()

    with pytest.raises(
        ma.MovingAverageInventoryError,
        match="cannot be negative",
    ):
        await ma.process_moving_average_receipt(
            db=db,
            company_id=1,
            document_id=10,
            document_line_id=11,
            product_id=20,
            warehouse_id=30,
            movement_date=date(
                2026,
                9,
                10,
            ),
            quantity=Decimal("1"),
            unit_cost=Decimal("1"),
            exact_valuation_amount=Decimal(
                value
            ),
        )

    assert called is False
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    (
        "NaN",
        "Infinity",
        "-Infinity",
    ),
)
async def test_nonfinite_exact_value_rejected_before_balance_lock(
    monkeypatch,
    value,
):
    called = False

    async def locked_balance(
        **kwargs,
    ):
        nonlocal called
        called = True
        return balance()

    monkeypatch.setattr(
        ma,
        "get_locked_moving_average_balance",
        locked_balance,
    )

    db = FakeDb()

    with pytest.raises(
        ma.MovingAverageInventoryError,
        match="must be finite",
    ):
        await ma.process_moving_average_receipt(
            db=db,
            company_id=1,
            document_id=10,
            document_line_id=11,
            product_id=20,
            warehouse_id=30,
            movement_date=date(
                2026,
                9,
                10,
            ),
            quantity=Decimal("1"),
            unit_cost=Decimal("1"),
            exact_valuation_amount=Decimal(
                value
            ),
        )

    assert called is False
    assert db.added == []


@pytest.mark.asyncio
async def test_zero_exact_value_is_valid_for_positive_quantity(
    monkeypatch,
):
    current = balance()

    result, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="2",
        unit_cost="0",
        exact_valuation_amount="0",
    )

    assert (
        movement.value_delta
        == Decimal("0.00000000")
    )

    assert (
        result.quantity
        == Decimal("2")
    )

    assert (
        result.inventory_value
        == Decimal("0.00000000")
    )

    assert (
        result.average_unit_cost
        == Decimal("0E-8")
        or result.average_unit_cost
        == Decimal("0.00000000")
    )


@pytest.mark.asyncio
async def test_exact_value_does_not_need_to_equal_rounded_price_product(
    monkeypatch,
):
    current = balance()

    _, movement = await run_receipt(
        monkeypatch,
        current_balance=current,
        quantity="3",
        unit_cost="3.3333",
        exact_valuation_amount="10",
    )

    assert (
        Decimal("3")
        * Decimal("3.3333")
    ) == Decimal("9.9999")

    assert (
        movement.value_delta
        == Decimal("10.00000000")
    )
