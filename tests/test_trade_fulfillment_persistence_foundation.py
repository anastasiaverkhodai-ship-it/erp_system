from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)


def _unique(
    table,
    name: str,
):
    return next(
        (
            constraint
            for constraint
            in table.constraints
            if (
                isinstance(
                    constraint,
                    UniqueConstraint,
                )
                and constraint.name == name
            )
        ),
        None,
    )


def _foreign_key(
    table,
    name: str,
):
    return next(
        (
            constraint
            for constraint
            in table.constraints
            if (
                isinstance(
                    constraint,
                    ForeignKeyConstraint,
                )
                and constraint.name == name
            )
        ),
        None,
    )


def test_document_has_typed_fulfillment_key() -> None:
    constraint = _unique(
        Document.__table__,
        (
            "uq_documents_company_id_"
            "id_document_type"
        ),
    )

    assert constraint is not None

    assert [
        column.name
        for column in constraint.columns
    ] == [
        "company_id",
        "id",
        "document_type",
    ]


def test_document_line_has_fulfillment_target_key() -> None:
    constraint = _unique(
        DocumentLine.__table__,
        (
            "uq_document_lines_"
            "fulfillment_target"
        ),
    )

    assert constraint is not None

    assert [
        column.name
        for column in constraint.columns
    ] == [
        "document_id",
        "id",
        "product_id",
        "warehouse_id",
    ]


def test_trade_fulfillment_table_name() -> None:
    assert (
        TradeFulfillment.__tablename__
        == "trade_fulfillments"
    )


def test_fulfillment_target_document_is_unique() -> None:
    constraint = _unique(
        TradeFulfillment.__table__,
        (
            "uq_trade_fulfillments_"
            "company_warehouse_document"
        ),
    )

    assert constraint is not None


def test_fulfillment_source_is_company_safe() -> None:
    constraint = _foreign_key(
        TradeFulfillment.__table__,
        (
            "fk_trade_fulfillments_"
            "company_trade_document"
        ),
    )

    assert constraint is not None

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "trade_documents.company_id",
        "trade_documents.id",
    ]


def test_fulfillment_target_is_company_and_type_safe() -> None:
    constraint = _foreign_key(
        TradeFulfillment.__table__,
        (
            "fk_trade_fulfillments_"
            "company_warehouse_document"
        ),
    )

    assert constraint is not None

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "documents.company_id",
        "documents.id",
        "documents.document_type",
    ]


def test_fulfillment_target_type_is_restricted() -> None:
    constraint = next(
        (
            constraint
            for constraint
            in TradeFulfillment.__table__.constraints
            if (
                isinstance(
                    constraint,
                    CheckConstraint,
                )
                and constraint.name
                == (
                    "ck_trade_fulfillment_"
                    "warehouse_document_type"
                )
            )
        ),
        None,
    )

    assert constraint is not None

    assert (
        str(
            constraint.sqltext
        )
        == (
            "warehouse_document_type "
            "IN ('issue', 'receipt')"
        )
    )


def test_fulfillment_target_type_has_no_default() -> None:
    column = (
        TradeFulfillment.__table__.c
        .warehouse_document_type
    )

    assert column.default is None
    assert column.server_default is None


def test_fulfillment_line_header_fk() -> None:
    constraint = _foreign_key(
        TradeFulfillmentLine.__table__,
        (
            "fk_trade_fulfillment_lines_"
            "fulfillment"
        ),
    )

    assert constraint is not None


def test_fulfillment_line_source_fk() -> None:
    constraint = _foreign_key(
        TradeFulfillmentLine.__table__,
        (
            "fk_trade_fulfillment_lines_"
            "trade_source"
        ),
    )

    assert constraint is not None

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "trade_document_lines.company_id",
        "trade_document_lines.trade_document_id",
        "trade_document_lines.id",
        "trade_document_lines.product_id",
        "trade_document_lines.warehouse_id",
    ]


def test_fulfillment_line_target_fk() -> None:
    constraint = _foreign_key(
        TradeFulfillmentLine.__table__,
        (
            "fk_trade_fulfillment_lines_"
            "warehouse_target"
        ),
    )

    assert constraint is not None

    assert [
        element.target_fullname
        for element
        in constraint.elements
    ] == [
        "document_lines.document_id",
        "document_lines.id",
        "document_lines.product_id",
        "document_lines.warehouse_id",
    ]


def test_warehouse_document_line_used_once() -> None:
    constraint = _unique(
        TradeFulfillmentLine.__table__,
        (
            "uq_trade_fulfillment_lines_"
            "warehouse_document_line"
        ),
    )

    assert constraint is not None


def test_fulfillment_quantity_positive_check() -> None:
    checks = {
        constraint.name
        for constraint
        in TradeFulfillmentLine
        .__table__
        .constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "ck_trade_fulfillment_line_"
        "quantity_positive"
        in checks
    )


def test_fulfillment_quantity_precision() -> None:
    column = (
        TradeFulfillmentLine
        .__table__
        .c
        .quantity
    )

    assert column.type.precision == 18
    assert column.type.scale == 4


def test_fulfillment_warehouse_required() -> None:
    assert (
        TradeFulfillmentLine
        .__table__
        .c
        .warehouse_id
        .nullable
        is False
    )
