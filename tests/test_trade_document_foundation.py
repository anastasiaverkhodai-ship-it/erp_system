from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.contract import Contract
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine


def _constraint_names(table) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if constraint.name is not None
    }


def test_contract_supports_trade_document_safe_fk() -> None:
    names = _constraint_names(
        Contract.__table__
    )

    assert (
        "uq_contracts_company_counterparty_id"
        in names
    )


def test_trade_document_core_columns() -> None:
    columns = TradeDocument.__table__.columns

    expected = {
        "id",
        "company_id",
        "counterparty_id",
        "contract_id",
        "number",
        "direction",
        "kind",
        "status",
        "document_date",
        "currency_code",
        "payment_term_days",
        "created_by",
        "created_at",
        "updated_at",
        "confirmed_at",
        "cancelled_at",
    }

    assert expected <= set(columns.keys())


def test_trade_document_number_uniqueness() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocument.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "uq_trade_document_"
            "company_direction_kind_number"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "direction",
        "kind",
        "number",
    ]


def test_trade_document_counterparty_fk() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocument.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_documents_"
            "company_counterparty"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "counterparty_id",
    ]

    assert [
        element.target_fullname
        for element in target.elements
    ] == [
        "counterparties.company_id",
        "counterparties.id",
    ]


def test_trade_document_contract_fk_is_counterparty_safe() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocument.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_documents_"
            "company_counterparty_contract"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "counterparty_id",
        "contract_id",
    ]

    assert [
        element.target_fullname
        for element in target.elements
    ] == [
        "contracts.company_id",
        "contracts.counterparty_id",
        "contracts.id",
    ]


def test_trade_document_constraints_exist() -> None:
    names = _constraint_names(
        TradeDocument.__table__
    )

    assert {
        "ck_trade_document_direction",
        "ck_trade_document_kind",
        "ck_trade_document_status",
        "ck_trade_document_currency_code_length",
        (
            "ck_trade_document_"
            "payment_term_days_nonnegative"
        ),
    } <= names


def test_trade_document_line_core_columns() -> None:
    columns = (
        TradeDocumentLine
        .__table__
        .columns
    )

    assert {
        "id",
        "trade_document_id",
        "line_number",
        "product_id",
        "warehouse_id",
        "quantity",
        "unit_price",
    } <= set(columns.keys())


def test_trade_document_line_position_unique() -> None:
    names = _constraint_names(
        TradeDocumentLine.__table__
    )

    assert (
        "uq_trade_document_line_"
        "document_line_number"
    ) in names


def test_trade_document_line_checks_exist() -> None:
    checks = {
        constraint.name
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        (
            "ck_trade_document_line_"
            "number_positive"
        ),
        (
            "ck_trade_document_line_"
            "quantity_positive"
        ),
        (
            "ck_trade_document_line_"
            "unit_price_nonnegative"
        ),
    } <= checks


def test_trade_document_line_cascade_fk() -> None:
    fk = next(
        foreign_key
        for foreign_key
        in TradeDocumentLine.__table__.foreign_keys
        if foreign_key.target_fullname
        == "trade_documents.id"
    )

    assert fk.ondelete == "CASCADE"


def test_product_supports_company_safe_fk() -> None:
    names = _constraint_names(
        Product.__table__
    )

    assert (
        "uq_products_company_id_id"
        in names
    )


def test_warehouse_supports_company_safe_fk() -> None:
    names = _constraint_names(
        Warehouse.__table__
    )

    assert (
        "uq_warehouses_company_id_id"
        in names
    )


def test_trade_document_line_has_company_id() -> None:
    assert (
        "company_id"
        in TradeDocumentLine.__table__.columns
    )


def test_trade_document_line_document_fk_is_company_safe() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_document_lines_"
            "company_document"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "trade_document_id",
    ]

    assert [
        element.target_fullname
        for element in target.elements
    ] == [
        "trade_documents.company_id",
        "trade_documents.id",
    ]

    assert target.ondelete == "CASCADE"


def test_trade_document_line_product_fk_is_company_safe() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_document_lines_"
            "company_product"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "product_id",
    ]

    assert [
        element.target_fullname
        for element in target.elements
    ] == [
        "products.company_id",
        "products.id",
    ]

    assert target.ondelete == "RESTRICT"


def test_trade_document_line_warehouse_fk_is_company_safe() -> None:
    constraints = [
        constraint
        for constraint
        in TradeDocumentLine.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    ]

    target = next(
        constraint
        for constraint in constraints
        if constraint.name
        == (
            "fk_trade_document_lines_"
            "company_warehouse"
        )
    )

    assert [
        column.name
        for column in target.columns
    ] == [
        "company_id",
        "warehouse_id",
    ]

    assert [
        element.target_fullname
        for element in target.elements
    ] == [
        "warehouses.company_id",
        "warehouses.id",
    ]

    assert target.ondelete == "RESTRICT"
