from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models.reservation_movement import (
    ReservationMovement,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.reservation_movement_definition import (
    ReservationMovementDefinition,
)
from app.services.reservation_types import (
    ReservationMovementType,
)


def _movement_definition(
    movement_type: ReservationMovementType,
) -> ReservationMovementDefinition:
    return ReservationMovementDefinition(
        company_id=1,
        product_id=2,
        warehouse_id=3,
        source_document_id=4,
        source_document_line_id=5,
        quantity=Decimal("10.0000"),
        movement_type=movement_type,
    )


def test_reservation_movement_types() -> None:
    assert {
        item.value
        for item in ReservationMovementType
    } == {
        "reserve",
        "release",
        "consume",
    }


def test_reserve_signed_quantity_positive() -> None:
    movement = _movement_definition(
        ReservationMovementType.RESERVE
    )

    assert (
        movement.signed_quantity
        == Decimal("10.0000")
    )


def test_release_signed_quantity_negative() -> None:
    movement = _movement_definition(
        ReservationMovementType.RELEASE
    )

    assert (
        movement.signed_quantity
        == Decimal("-10.0000")
    )


def test_consume_signed_quantity_negative() -> None:
    movement = _movement_definition(
        ReservationMovementType.CONSUME
    )

    assert (
        movement.signed_quantity
        == Decimal("-10.0000")
    )


def test_persistent_reservation_table_name() -> None:
    assert (
        ReservationMovement.__tablename__
        == "reservation_movements"
    )


def test_trade_line_has_reservation_source_unique() -> None:
    constraint = next(
        (
            item
            for item
            in TradeDocumentLine.__table__.constraints
            if (
                isinstance(
                    item,
                    UniqueConstraint,
                )
                and item.name
                == (
                    "uq_trade_document_lines_"
                    "reservation_source"
                )
            )
        ),
        None,
    )

    assert constraint is not None

    assert [
        column.name
        for column
        in constraint.columns
    ] == [
        "company_id",
        "trade_document_id",
        "id",
        "product_id",
        "warehouse_id",
    ]


def test_reservation_source_fk_is_company_safe() -> None:
    constraint = next(
        (
            item
            for item
            in ReservationMovement
            .__table__
            .constraints
            if (
                isinstance(
                    item,
                    ForeignKeyConstraint,
                )
                and item.name
                == (
                    "fk_reservation_movements_"
                    "trade_document_line"
                )
            )
        ),
        None,
    )

    assert constraint is not None

    assert [
        column.name
        for column
        in constraint.columns
    ] == [
        "company_id",
        "source_document_id",
        "source_document_line_id",
        "product_id",
        "warehouse_id",
    ]

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


def test_reservation_quantity_positive_check() -> None:
    checks = {
        item.name
        for item
        in ReservationMovement.__table__.constraints
        if isinstance(
            item,
            CheckConstraint,
        )
    }

    assert (
        "ck_reservation_movement_quantity_positive"
        in checks
    )


def test_reservation_movement_type_check() -> None:
    checks = {
        item.name
        for item
        in ReservationMovement.__table__.constraints
        if isinstance(
            item,
            CheckConstraint,
        )
    }

    assert (
        "ck_reservation_movement_type"
        in checks
    )


def test_reservation_indexes_exist() -> None:
    indexes = {
        item.name
        for item
        in ReservationMovement.__table__.indexes
        if isinstance(
            item,
            Index,
        )
    }

    assert {
        "ix_reservation_movements_stock",
        "ix_reservation_movements_source_line",
        "ix_reservation_movements_source_document",
    } <= indexes


def test_reservation_quantity_precision() -> None:
    column = (
        ReservationMovement
        .__table__
        .c
        .quantity
    )

    assert column.type.precision == 18
    assert column.type.scale == 4


def test_reservation_warehouse_is_required() -> None:
    column = (
        ReservationMovement
        .__table__
        .c
        .warehouse_id
    )

    assert column.nullable is False
