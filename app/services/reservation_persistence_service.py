from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reservation_movement import (
    ReservationMovement,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.reservation_types import (
    ReservationMovementType,
)
from app.services.warehouse_posting import (
    get_locked_stock_balance,
)


ZERO = Decimal("0")


class ReservationPersistenceError(Exception):
    """Base error for persistent reservations."""


class ReservationSourceLineNotFoundError(
    ReservationPersistenceError
):
    """Source TradeDocumentLine does not exist."""


class ReservationWarehouseRequiredError(
    ReservationPersistenceError
):
    """Reservation requires a concrete warehouse."""


class InvalidPersistentReservationBalanceError(
    ReservationPersistenceError
):
    """Stored reservation state is internally invalid."""


class InsufficientPersistentAvailableStockError(
    ReservationPersistenceError
):
    """Not enough physical unreserved stock."""


class ReservationExceedsSourceQuantityError(
    ReservationPersistenceError
):
    """Reservation would exceed source line quantity."""


class InsufficientSourceReservationError(
    ReservationPersistenceError
):
    """Release/consume exceeds source line reservation."""


@dataclass(
    frozen=True,
    slots=True,
)
class ReservationTransition:
    physical_quantity: Decimal
    stock_reserved_before: Decimal
    source_reserved_before: Decimal
    stock_reserved_after: Decimal
    source_reserved_after: Decimal

    @property
    def available_before(
        self,
    ) -> Decimal:
        return (
            self.physical_quantity
            - self.stock_reserved_before
        )

    @property
    def available_after(
        self,
    ) -> Decimal:
        return (
            self.physical_quantity
            - self.stock_reserved_after
        )


def validate_reservation_transition(
    *,
    movement_type: ReservationMovementType,
    quantity: Decimal,
    physical_quantity: Decimal,
    stock_reserved_quantity: Decimal,
    source_reserved_quantity: Decimal,
    source_quantity: Decimal,
) -> ReservationTransition:
    if quantity <= ZERO:
        raise ValueError(
            "Reservation movement quantity "
            "must be greater than zero"
        )

    if physical_quantity < ZERO:
        raise InvalidPersistentReservationBalanceError(
            "Physical stock cannot be negative"
        )

    if stock_reserved_quantity < ZERO:
        raise InvalidPersistentReservationBalanceError(
            "Stock reserved quantity cannot be negative"
        )

    if source_reserved_quantity < ZERO:
        raise InvalidPersistentReservationBalanceError(
            "Source reserved quantity cannot be negative"
        )

    if source_quantity <= ZERO:
        raise InvalidPersistentReservationBalanceError(
            "Source quantity must be greater than zero"
        )

    if (
        stock_reserved_quantity
        > physical_quantity
    ):
        raise InvalidPersistentReservationBalanceError(
            "Reserved stock exceeds physical stock"
        )

    if (
        source_reserved_quantity
        > source_quantity
    ):
        raise InvalidPersistentReservationBalanceError(
            "Source reservation exceeds source quantity"
        )

    if (
        movement_type
        == ReservationMovementType.RESERVE
    ):
        available_quantity = (
            physical_quantity
            - stock_reserved_quantity
        )

        if quantity > available_quantity:
            raise (
                InsufficientPersistentAvailableStockError(
                    "Insufficient available stock: "
                    f"requested={quantity}, "
                    f"available={available_quantity}"
                )
            )

        new_source_reserved = (
            source_reserved_quantity
            + quantity
        )

        if new_source_reserved > source_quantity:
            raise ReservationExceedsSourceQuantityError(
                "Reservation exceeds source line quantity: "
                f"requested={quantity}, "
                f"already_reserved="
                f"{source_reserved_quantity}, "
                f"source_quantity={source_quantity}"
            )

        new_stock_reserved = (
            stock_reserved_quantity
            + quantity
        )

    else:
        if quantity > source_reserved_quantity:
            raise InsufficientSourceReservationError(
                "Reservation movement exceeds "
                "source line reserved quantity: "
                f"requested={quantity}, "
                f"reserved={source_reserved_quantity}"
            )

        if quantity > stock_reserved_quantity:
            raise InvalidPersistentReservationBalanceError(
                "Reservation movement exceeds "
                "stock reserved quantity"
            )

        new_stock_reserved = (
            stock_reserved_quantity
            - quantity
        )

        new_source_reserved = (
            source_reserved_quantity
            - quantity
        )

    return ReservationTransition(
        physical_quantity=physical_quantity,
        stock_reserved_before=(
            stock_reserved_quantity
        ),
        source_reserved_before=(
            source_reserved_quantity
        ),
        stock_reserved_after=(
            new_stock_reserved
        ),
        source_reserved_after=(
            new_source_reserved
        ),
    )


def _signed_quantity_expression():
    return case(
        (
            ReservationMovement.movement_type
            == ReservationMovementType.RESERVE,
            ReservationMovement.quantity,
        ),
        else_=-ReservationMovement.quantity,
    )


async def get_reserved_quantity_for_stock(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> Decimal:
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    _signed_quantity_expression()
                ),
                ZERO,
            )
        ).where(
            ReservationMovement.company_id
            == company_id,
            ReservationMovement.product_id
            == product_id,
            ReservationMovement.warehouse_id
            == warehouse_id,
        )
    )

    value = result.scalar_one()

    return Decimal(value)


async def get_reserved_quantity_for_source_line(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
) -> Decimal:
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    _signed_quantity_expression()
                ),
                ZERO,
            )
        ).where(
            ReservationMovement.company_id
            == company_id,
            ReservationMovement.source_document_id
            == source_document_id,
            ReservationMovement.source_document_line_id
            == source_document_line_id,
        )
    )

    value = result.scalar_one()

    return Decimal(value)


async def get_locked_source_line(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
) -> TradeDocumentLine:
    result = await db.execute(
        select(
            TradeDocumentLine
        )
        .where(
            TradeDocumentLine.company_id
            == company_id,
            TradeDocumentLine.trade_document_id
            == source_document_id,
            TradeDocumentLine.id
            == source_document_line_id,
        )
        .with_for_update()
    )

    line = result.scalar_one_or_none()

    if line is None:
        raise ReservationSourceLineNotFoundError(
            "Trade document source line not found"
        )

    return line


async def append_reservation_movement(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
    quantity: Decimal,
    movement_type: ReservationMovementType,
) -> ReservationMovement:
    """
    Append one reservation movement.

    The caller owns commit/rollback.

    Lock order:
        1. source TradeDocumentLine
        2. StockBalance

    This serializes concurrent operations for the same
    source line and physical stock key.
    """

    line = await get_locked_source_line(
        db,
        company_id=company_id,
        source_document_id=source_document_id,
        source_document_line_id=(
            source_document_line_id
        ),
    )

    if line.warehouse_id is None:
        raise ReservationWarehouseRequiredError(
            "Trade document line must have a warehouse "
            "before it can be reserved"
        )

    stock_balance = await get_locked_stock_balance(
        db,
        company_id=company_id,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
    )

    stock_reserved_quantity = (
        await get_reserved_quantity_for_stock(
            db,
            company_id=company_id,
            product_id=line.product_id,
            warehouse_id=line.warehouse_id,
        )
    )

    source_reserved_quantity = (
        await get_reserved_quantity_for_source_line(
            db,
            company_id=company_id,
            source_document_id=(
                source_document_id
            ),
            source_document_line_id=(
                source_document_line_id
            ),
        )
    )

    validate_reservation_transition(
        movement_type=movement_type,
        quantity=quantity,
        physical_quantity=Decimal(
            stock_balance.quantity
        ),
        stock_reserved_quantity=(
            stock_reserved_quantity
        ),
        source_reserved_quantity=(
            source_reserved_quantity
        ),
        source_quantity=Decimal(
            line.quantity
        ),
    )

    movement = ReservationMovement(
        company_id=company_id,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        source_document_id=(
            source_document_id
        ),
        source_document_line_id=(
            source_document_line_id
        ),
        quantity=quantity,
        movement_type=movement_type,
    )

    db.add(
        movement
    )

    # Flush makes the movement visible to subsequent
    # aggregate queries in the same transaction and
    # assigns movement.id.
    await db.flush()

    return movement


async def reserve_source_line(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
    quantity: Decimal,
) -> ReservationMovement:
    return await append_reservation_movement(
        db,
        company_id=company_id,
        source_document_id=source_document_id,
        source_document_line_id=(
            source_document_line_id
        ),
        quantity=quantity,
        movement_type=(
            ReservationMovementType.RESERVE
        ),
    )


async def release_source_line(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
    quantity: Decimal,
) -> ReservationMovement:
    return await append_reservation_movement(
        db,
        company_id=company_id,
        source_document_id=source_document_id,
        source_document_line_id=(
            source_document_line_id
        ),
        quantity=quantity,
        movement_type=(
            ReservationMovementType.RELEASE
        ),
    )


async def consume_source_line_reservation(
    db: AsyncSession,
    *,
    company_id: int,
    source_document_id: int,
    source_document_line_id: int,
    quantity: Decimal,
) -> ReservationMovement:
    return await append_reservation_movement(
        db,
        company_id=company_id,
        source_document_id=source_document_id,
        source_document_line_id=(
            source_document_line_id
        ),
        quantity=quantity,
        movement_type=(
            ReservationMovementType.CONSUME
        ),
    )
