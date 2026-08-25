from decimal import Decimal

import pytest

from app.services.reservation_persistence_service import (
    InsufficientPersistentAvailableStockError,
    InsufficientSourceReservationError,
    InvalidPersistentReservationBalanceError,
    ReservationExceedsSourceQuantityError,
    validate_reservation_transition,
)
from app.services.reservation_types import (
    ReservationMovementType,
)


def test_reserve_valid_transition() -> None:
    result = validate_reservation_transition(
        movement_type=(
            ReservationMovementType.RESERVE
        ),
        quantity=Decimal("3"),
        physical_quantity=Decimal("10"),
        stock_reserved_quantity=Decimal("2"),
        source_reserved_quantity=Decimal("1"),
        source_quantity=Decimal("6"),
    )

    assert (
        result.stock_reserved_after
        == Decimal("5")
    )

    assert (
        result.source_reserved_after
        == Decimal("4")
    )

    assert (
        result.available_before
        == Decimal("8")
    )

    assert (
        result.available_after
        == Decimal("5")
    )


def test_reserve_rejects_insufficient_stock() -> None:
    with pytest.raises(
        InsufficientPersistentAvailableStockError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RESERVE
            ),
            quantity=Decimal("4"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("7"),
            source_reserved_quantity=Decimal("0"),
            source_quantity=Decimal("10"),
        )


def test_reserve_rejects_source_over_reservation() -> None:
    with pytest.raises(
        ReservationExceedsSourceQuantityError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RESERVE
            ),
            quantity=Decimal("3"),
            physical_quantity=Decimal("100"),
            stock_reserved_quantity=Decimal("0"),
            source_reserved_quantity=Decimal("8"),
            source_quantity=Decimal("10"),
        )


def test_release_valid_transition() -> None:
    result = validate_reservation_transition(
        movement_type=(
            ReservationMovementType.RELEASE
        ),
        quantity=Decimal("2"),
        physical_quantity=Decimal("10"),
        stock_reserved_quantity=Decimal("7"),
        source_reserved_quantity=Decimal("5"),
        source_quantity=Decimal("5"),
    )

    assert (
        result.stock_reserved_after
        == Decimal("5")
    )

    assert (
        result.source_reserved_after
        == Decimal("3")
    )


def test_consume_valid_transition() -> None:
    result = validate_reservation_transition(
        movement_type=(
            ReservationMovementType.CONSUME
        ),
        quantity=Decimal("3"),
        physical_quantity=Decimal("10"),
        stock_reserved_quantity=Decimal("6"),
        source_reserved_quantity=Decimal("4"),
        source_quantity=Decimal("5"),
    )

    assert (
        result.stock_reserved_after
        == Decimal("3")
    )

    assert (
        result.source_reserved_after
        == Decimal("1")
    )


def test_release_rejects_more_than_source_reserved() -> None:
    with pytest.raises(
        InsufficientSourceReservationError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RELEASE
            ),
            quantity=Decimal("4"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("5"),
            source_reserved_quantity=Decimal("3"),
            source_quantity=Decimal("10"),
        )


def test_consume_rejects_more_than_source_reserved() -> None:
    with pytest.raises(
        InsufficientSourceReservationError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.CONSUME
            ),
            quantity=Decimal("4"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("5"),
            source_reserved_quantity=Decimal("3"),
            source_quantity=Decimal("10"),
        )


def test_negative_stock_reservation_is_invalid() -> None:
    with pytest.raises(
        InvalidPersistentReservationBalanceError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RESERVE
            ),
            quantity=Decimal("1"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("-1"),
            source_reserved_quantity=Decimal("0"),
            source_quantity=Decimal("5"),
        )


def test_reserved_stock_cannot_exceed_physical() -> None:
    with pytest.raises(
        InvalidPersistentReservationBalanceError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RELEASE
            ),
            quantity=Decimal("1"),
            physical_quantity=Decimal("5"),
            stock_reserved_quantity=Decimal("6"),
            source_reserved_quantity=Decimal("2"),
            source_quantity=Decimal("5"),
        )


def test_source_reservation_cannot_exceed_source_quantity() -> None:
    with pytest.raises(
        InvalidPersistentReservationBalanceError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RELEASE
            ),
            quantity=Decimal("1"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("8"),
            source_reserved_quantity=Decimal("6"),
            source_quantity=Decimal("5"),
        )


def test_quantity_must_be_positive() -> None:
    with pytest.raises(
        ValueError
    ):
        validate_reservation_transition(
            movement_type=(
                ReservationMovementType.RESERVE
            ),
            quantity=Decimal("0"),
            physical_quantity=Decimal("10"),
            stock_reserved_quantity=Decimal("0"),
            source_reserved_quantity=Decimal("0"),
            source_quantity=Decimal("5"),
        )
