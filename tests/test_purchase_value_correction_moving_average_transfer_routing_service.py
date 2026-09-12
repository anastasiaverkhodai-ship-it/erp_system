from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.company import InventoryValuationMethod
from app.services.purchase_value_correction_moving_average_transfer_routing_service import (
    PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
    build_moving_average_transfer_route_from_rows,
)


MA = (
    InventoryValuationMethod
    .WEIGHTED_AVERAGE_MOVING
)


def layer(**overrides):
    values = {
        "id": 101,
        "company_id": 1,
        "transfer_line_id": 201,
        "product_id": 7,
        "destination_warehouse_id": 3,
        "valuation_method": MA,
        "quantity": Decimal("30.0000"),
        "unit_cost": Decimal("3.33333333"),
        "valuation_amount": Decimal("99.99999990"),
        "source_inventory_cost_entry_id": 301,
        "source_stock_lot_consumption_id": None,
        "destination_receipt_document_id": 401,
        "destination_receipt_document_line_id": 402,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def destination_line(**overrides):
    values = {
        "id": 402,
        "document_id": 401,
        "product_id": 7,
        "warehouse_id": 3,
        "price": Decimal("3.3333"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def destination_ice(**overrides):
    values = {
        "id": 501,
        "company_id": 1,
        "document_id": 401,
        "document_line_id": 402,
        "valuation_method": MA,
        "quantity": Decimal("30.0000"),
        "unit_cost": Decimal("3.33333333"),
        "valuation_amount": Decimal("99.99999990"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def build(
    *,
    layer_row=None,
    line_row=None,
    ice_row=None,
):
    return build_moving_average_transfer_route_from_rows(
        layer=layer_row or layer(),
        destination_line=line_row or destination_line(),
        destination_inventory_cost_entry=(
            ice_row or destination_ice()
        ),
    )


def test_builds_exact_ma_transfer_route():
    route = build()

    assert route.warehouse_transfer_valuation_layer_id == 101
    assert route.source_inventory_cost_entry_id == 301
    assert route.destination_receipt_document_id == 401
    assert route.destination_receipt_document_line_id == 402
    assert route.destination_inventory_cost_entry_id == 501

    assert route.quantity == Decimal("30.0000")
    assert route.unit_cost == Decimal("3.33333333")
    assert route.valuation_amount == Decimal("99.99999990")


def test_document_line_q4_price_is_not_authoritative():
    route = build(
        line_row=destination_line(
            price=Decimal("9999.9999")
        )
    )

    assert route.unit_cost == Decimal("3.33333333")
    assert route.valuation_amount == Decimal("99.99999990")


def test_rejects_fifo_transfer_layer():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="not moving average",
    ):
        build(
            layer_row=layer(
                valuation_method=InventoryValuationMethod.FIFO,
                source_stock_lot_consumption_id=999,
            )
        )


def test_rejects_fifo_consumption_provenance():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="must not reference",
    ):
        build(
            layer_row=layer(
                source_stock_lot_consumption_id=999
            )
        )


@pytest.mark.parametrize(
    ("line_overrides", "message"),
    (
        (
            {"id": 999},
            "does not match WTVL provenance",
        ),
        (
            {"document_id": 999},
            "does not match WTVL provenance",
        ),
        (
            {"product_id": 999},
            "product/warehouse",
        ),
        (
            {"warehouse_id": 999},
            "product/warehouse",
        ),
    ),
)
def test_rejects_destination_line_provenance_mismatch(
    line_overrides,
    message,
):
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match=message,
    ):
        build(
            line_row=destination_line(
                **line_overrides
            )
        )


def test_rejects_destination_ice_company_mismatch():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="another company",
    ):
        build(
            ice_row=destination_ice(
                company_id=2
            )
        )


@pytest.mark.parametrize(
    "ice_overrides",
    (
        {"document_id": 999},
        {"document_line_id": 999},
    ),
)
def test_rejects_destination_ice_provenance_mismatch(
    ice_overrides,
):
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="does not match",
    ):
        build(
            ice_row=destination_ice(
                **ice_overrides
            )
        )


def test_rejects_non_ma_destination_ice():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="not moving average",
    ):
        build(
            ice_row=destination_ice(
                valuation_method=InventoryValuationMethod.FIFO
            )
        )


@pytest.mark.parametrize(
    ("ice_overrides", "message"),
    (
        (
            {"quantity": Decimal("29.0000")},
            "quantity differs",
        ),
        (
            {"unit_cost": Decimal("3.33333332")},
            "unit cost differs",
        ),
        (
            {
                "valuation_amount": Decimal(
                    "99.99999989"
                )
            },
            "valuation amount differs",
        ),
    ),
)
def test_rejects_destination_ice_q8_mismatch(
    ice_overrides,
    message,
):
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match=message,
    ):
        build(
            ice_row=destination_ice(
                **ice_overrides
            )
        )


def test_rejects_nonpositive_layer_quantity():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError,
        match="quantity must be positive",
    ):
        build(
            layer_row=layer(
                quantity=Decimal("0")
            ),
            ice_row=destination_ice(
                quantity=Decimal("0")
            ),
        )


def test_route_is_immutable():
    route = build()

    with pytest.raises(
        AttributeError
    ):
        route.quantity = Decimal("1")
