from sqlalchemy import (
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.warehouse_transfer_valuation_layer import (
    WarehouseTransferValuationLayer,
)


def _unique_sets(table):
    return {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }


def _fk_names(table):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
        and constraint.name is not None
    }


def test_transfer_valuation_layer_exact_columns():
    table = WarehouseTransferValuationLayer.__table__

    assert set(table.columns.keys()) == {
        "id",
        "company_id",
        "transfer_line_id",
        "product_id",
        "destination_warehouse_id",
        "valuation_method",
        "quantity",
        "unit_cost",
        "valuation_amount",
        "source_inventory_cost_entry_id",
        "source_stock_lot_consumption_id",
        "destination_receipt_document_id",
        "destination_receipt_document_line_id",
        "created_at",
    }


def test_destination_receipt_line_is_one_layer_only():
    assert (
        "destination_receipt_document_line_id",
    ) in _unique_sets(
        WarehouseTransferValuationLayer.__table__
    )


def test_fifo_consumption_cannot_be_transferred_twice():
    assert (
        "source_stock_lot_consumption_id",
    ) in _unique_sets(
        WarehouseTransferValuationLayer.__table__
    )


def test_layer_has_exact_transfer_parent_fk():
    assert (
        "fk_wtvl_transfer_line_identity"
        in _fk_names(
            WarehouseTransferValuationLayer.__table__
        )
    )


def test_layer_has_exact_destination_line_fk():
    assert (
        "fk_wtvl_destination_receipt_line"
        in _fk_names(
            WarehouseTransferValuationLayer.__table__
        )
    )


def test_fifo_consumption_nullable_for_moving_average():
    column = (
        WarehouseTransferValuationLayer
        .__table__
        .columns[
            "source_stock_lot_consumption_id"
        ]
    )

    assert column.nullable is True


def test_source_issue_ice_is_required():
    column = (
        WarehouseTransferValuationLayer
        .__table__
        .columns[
            "source_inventory_cost_entry_id"
        ]
    )

    assert column.nullable is False
