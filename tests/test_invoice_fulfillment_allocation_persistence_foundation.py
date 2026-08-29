import app.models  # noqa: F401

from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
)

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)


def _unique_column_sets(table):
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


def _fk_signature(constraint):
    return tuple(
        (
            element.parent.name,
            element.target_fullname,
        )
        for element in constraint.elements
    )


def test_allocation_status_values():
    assert {
        item.value
        for item
        in InvoiceFulfillmentAllocationStatus
    } == {
        "active",
        "reversed",
    }


def test_allocation_table_name_and_columns():
    table = (
        InvoiceFulfillmentAllocation.__table__
    )

    assert table.name == (
        "invoice_fulfillment_allocations"
    )

    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "company_id",
        "invoice_id",
        "invoice_line_id",
        "fulfillment_id",
        "fulfillment_line_id",
        "order_id",
        "order_line_id",
        "product_id",
        "quantity",
        "status",
        "created_by",
        "created_at",
        "reversed_by",
        "reversed_at",
    }


def test_allocation_has_no_money_snapshots():
    columns = {
        column.name
        for column
        in InvoiceFulfillmentAllocation
        .__table__
        .columns
    }

    for forbidden in (
        "amount",
        "invoice_amount",
        "recognized_amount",
        "unit_price",
        "invoice_unit_price",
        "fulfillment_unit_price",
    ):
        assert forbidden not in columns


def test_quantity_precision():
    quantity = (
        InvoiceFulfillmentAllocation
        .__table__
        .c
        .quantity
    )

    assert quantity.type.precision == 18
    assert quantity.type.scale == 4


def test_trade_document_line_matching_identity():
    unique_sets = _unique_column_sets(
        TradeDocumentLine.__table__
    )

    assert (
        "company_id",
        "trade_document_id",
        "id",
        "product_id",
    ) in unique_sets


def test_trade_fulfillment_line_matching_identity():
    unique_sets = _unique_column_sets(
        TradeFulfillmentLine.__table__
    )

    assert (
        "company_id",
        "fulfillment_id",
        "trade_document_id",
        "trade_document_line_id",
        "id",
        "product_id",
    ) in unique_sets


def test_invoice_line_fk_is_company_safe():
    table = (
        InvoiceFulfillmentAllocation.__table__
    )

    signatures = {
        _fk_signature(constraint)
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        (
            "company_id",
            "trade_document_lines.company_id",
        ),
        (
            "invoice_id",
            "trade_document_lines.trade_document_id",
        ),
        (
            "invoice_line_id",
            "trade_document_lines.id",
        ),
        (
            "product_id",
            "trade_document_lines.product_id",
        ),
    ) in signatures


def test_fulfillment_line_fk_is_company_safe():
    table = (
        InvoiceFulfillmentAllocation.__table__
    )

    signatures = {
        _fk_signature(constraint)
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        (
            "company_id",
            "trade_fulfillment_lines.company_id",
        ),
        (
            "fulfillment_id",
            "trade_fulfillment_lines.fulfillment_id",
        ),
        (
            "order_id",
            "trade_fulfillment_lines.trade_document_id",
        ),
        (
            "order_line_id",
            "trade_fulfillment_lines.trade_document_line_id",
        ),
        (
            "fulfillment_line_id",
            "trade_fulfillment_lines.id",
        ),
        (
            "product_id",
            "trade_fulfillment_lines.product_id",
        ),
    ) in signatures


def test_allocation_check_constraints():
    checks = {
        str(constraint.sqltext)
        for constraint
        in InvoiceFulfillmentAllocation
        .__table__
        .constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    combined = " ".join(checks)

    assert "quantity > 0" in combined
    assert "invoice_id <> order_id" in combined
    assert "active" in combined
    assert "reversed" in combined
    assert "reversed_by" in combined
    assert "reversed_at" in combined


def test_active_pair_is_unique():
    indexes = {
        index.name: index
        for index
        in InvoiceFulfillmentAllocation
        .__table__
        .indexes
    }

    index = indexes[
        "uq_if_alloc_active_pair"
    ]

    assert index.unique is True

    assert [
        column.name
        for column in index.columns
    ] == [
        "company_id",
        "invoice_line_id",
        "fulfillment_line_id",
    ]

    where = (
        index
        .dialect_options["postgresql"]
        .get("where")
    )

    assert where is not None
    assert (
        "status = 'active'"
        in str(where)
    )


def test_active_lookup_indexes_exist():
    names = {
        index.name
        for index
        in InvoiceFulfillmentAllocation
        .__table__
        .indexes
    }

    assert (
        "ix_if_alloc_invoice_active"
        in names
    )

    assert (
        "ix_if_alloc_fulfillment_active"
        in names
    )
