from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_fifo_impact_persistence_service import (
    _transfer_destination_stock_lot_matches_allocation_source,
)


def ns(
    **values,
):
    return SimpleNamespace(
        **values
    )


def valid_objects():
    destination_stock_lot = ns(
        id=200,
        company_id=1,
        product_id=7,
        warehouse_id=22,
        source_document_id=501,
        source_document_line_id=502,
    )

    source_stock_lot = ns(
        id=100,
        company_id=1,
        product_id=7,
        source_document_line_id=300,
    )

    source_consumption = ns(
        id=400,
        company_id=1,
        stock_lot_id=100,
        quantity="50.0000",
    )

    transfer_layer = ns(
        company_id=1,
        valuation_method="fifo",
        source_stock_lot_consumption_id=400,
        destination_receipt_document_id=501,
        destination_receipt_document_line_id=502,
        product_id=7,
        destination_warehouse_id=22,
        quantity="50.0000",
    )

    fulfillment_line = ns(
        warehouse_document_line_id=300,
    )

    return {
        "destination_stock_lot": (
            destination_stock_lot
        ),
        "source_stock_lot": (
            source_stock_lot
        ),
        "source_consumption": (
            source_consumption
        ),
        "transfer_layer": (
            transfer_layer
        ),
        "fulfillment_line": (
            fulfillment_line
        ),
    }


def matches(
    values,
):
    return (
        _transfer_destination_stock_lot_matches_allocation_source(
            company_id=1,
            transfer_layer=values[
                "transfer_layer"
            ],
            destination_stock_lot=values[
                "destination_stock_lot"
            ],
            source_consumption=values[
                "source_consumption"
            ],
            source_stock_lot=values[
                "source_stock_lot"
            ],
            fulfillment_line=values[
                "fulfillment_line"
            ],
        )
    )


def test_exact_fifo_transfer_provenance_is_valid():
    values = valid_objects()

    assert matches(
        values
    ) is True


@pytest.mark.parametrize(
    (
        "object_name",
        "field",
        "wrong_value",
    ),
    (
        (
            "transfer_layer",
            "company_id",
            2,
        ),
        (
            "transfer_layer",
            "valuation_method",
            "moving_average",
        ),
        (
            "transfer_layer",
            "source_stock_lot_consumption_id",
            999,
        ),
        (
            "transfer_layer",
            "destination_receipt_document_id",
            999,
        ),
        (
            "transfer_layer",
            "destination_receipt_document_line_id",
            999,
        ),
        (
            "transfer_layer",
            "product_id",
            999,
        ),
        (
            "transfer_layer",
            "destination_warehouse_id",
            999,
        ),
        (
            "transfer_layer",
            "quantity",
            "49.0000",
        ),
        (
            "source_consumption",
            "company_id",
            2,
        ),
        (
            "source_consumption",
            "stock_lot_id",
            999,
        ),
        (
            "source_stock_lot",
            "company_id",
            2,
        ),
        (
            "source_stock_lot",
            "product_id",
            999,
        ),
        (
            "source_stock_lot",
            "source_document_line_id",
            999,
        ),
        (
            "destination_stock_lot",
            "product_id",
            999,
        ),
        (
            "destination_stock_lot",
            "warehouse_id",
            999,
        ),
        (
            "destination_stock_lot",
            "source_document_id",
            999,
        ),
        (
            "destination_stock_lot",
            "source_document_line_id",
            999,
        ),
    ),
)
def test_any_broken_transfer_provenance_is_rejected(
    object_name,
    field,
    wrong_value,
):
    values = valid_objects()

    obj = values[
        object_name
    ]

    setattr(
        obj,
        field,
        wrong_value,
    )

    assert matches(
        values
    ) is False
