from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.stock_lot import StockLot
from app.models.stock_lot_consumption import StockLotConsumption


class FIFOInventoryError(Exception):
    """Business error raised by FIFO inventory operations."""


async def create_stock_lot(
    db: AsyncSession,
    document: Document,
    line: DocumentLine,
    quantity: Decimal,
) -> StockLot:
    if quantity <= Decimal("0"):
        raise FIFOInventoryError(
            "FIFO lot quantity must be greater than zero"
        )

    if line.price < Decimal("0"):
        raise FIFOInventoryError(
            "FIFO lot unit cost cannot be negative"
        )

    stock_lot = StockLot(
        company_id=document.company_id,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        source_document_id=document.id,
        source_document_line_id=line.id,
        received_date=document.document_date,
        original_quantity=quantity,
        remaining_quantity=quantity,
        unit_cost=line.price,
    )

    db.add(stock_lot)

    return stock_lot

async def consume_fifo(
    db: AsyncSession,
    document: Document,
    line: DocumentLine,
    quantity: Decimal,
) -> list[StockLotConsumption]:
    if quantity <= Decimal("0"):
        raise FIFOInventoryError(
            "FIFO consumption quantity must be greater than zero"
        )

    lots_result = await db.execute(
        select(StockLot)
        .where(
            StockLot.company_id == document.company_id,
            StockLot.product_id == line.product_id,
            StockLot.warehouse_id == line.warehouse_id,
            StockLot.remaining_quantity > Decimal("0"),
        )
        .order_by(
            StockLot.received_date,
            StockLot.id,
        )
        .with_for_update()
    )

    stock_lots = lots_result.scalars().all()
    available_fifo_quantity = sum(
    (
        Decimal(stock_lot.remaining_quantity)
        for stock_lot in stock_lots
    ),
    Decimal("0"),
)

    if available_fifo_quantity < quantity:
     raise FIFOInventoryError(
        f"Insufficient FIFO stock for product "
        f"{line.product_id}. "
        f"Available in FIFO lots: "
        f"{available_fifo_quantity}, "
        f"required: {quantity}"
    )
    remaining_to_consume = Decimal(quantity)

    consumptions: list[StockLotConsumption] = []

    for stock_lot in stock_lots:
        if remaining_to_consume <= Decimal("0"):
            break

        available_quantity = Decimal(
            stock_lot.remaining_quantity
        )

        consumed_quantity = min(
            available_quantity,
            remaining_to_consume,
        )

        if consumed_quantity <= Decimal("0"):
            continue

        stock_lot.remaining_quantity = (
            available_quantity
            - consumed_quantity
        )

        consumption = StockLotConsumption(
            company_id=document.company_id,
            issue_document_id=document.id,
            issue_document_line_id=line.id,
            stock_lot_id=stock_lot.id,
            quantity=consumed_quantity,
            unit_cost=stock_lot.unit_cost,
        )

        db.add(consumption)
        consumptions.append(consumption)

        remaining_to_consume -= consumed_quantity

    return consumptions

def calculate_fifo_cost(
    consumptions: list[StockLotConsumption],
) -> Decimal:
    return sum(
        (
            Decimal(consumption.quantity)
            * Decimal(consumption.unit_cost)
            for consumption in consumptions
        ),
        Decimal("0"),
    )

async def reverse_receipt_fifo(
    db: AsyncSession,
    document: Document,
) -> None:
    lots_result = await db.execute(
        select(StockLot)
        .where(
            StockLot.company_id == document.company_id,
            StockLot.source_document_id == document.id,
        )
        .order_by(StockLot.id)
        .with_for_update()
    )

    stock_lots = lots_result.scalars().all()

    if not stock_lots:
        raise FIFOInventoryError(
            "Receipt document has no FIFO stock lots"
        )

    for stock_lot in stock_lots:
        original_quantity = Decimal(
            stock_lot.original_quantity
        )

        remaining_quantity = Decimal(
            stock_lot.remaining_quantity
        )

        if remaining_quantity != original_quantity:
            raise FIFOInventoryError(
                f"Cannot reverse receipt document because "
                f"FIFO lot {stock_lot.id} has already been "
                f"partially or fully consumed"
            )

    for stock_lot in stock_lots:
        stock_lot.remaining_quantity = Decimal("0")

async def reverse_issue_fifo(
    db: AsyncSession,
    document: Document,
) -> None:
    consumptions = (
    await db.scalars(
        select(StockLotConsumption)
        .where(
            StockLotConsumption.company_id
            == document.company_id,
            StockLotConsumption.issue_document_id
            == document.id,
        )
        .order_by(StockLotConsumption.id)
        .with_for_update()
    )
).all()

    if not consumptions:
        raise FIFOInventoryError(
            "Issue document has no FIFO consumptions"
        )

    quantities_by_lot: dict[int, Decimal] = {}

    for consumption in consumptions:
        quantities_by_lot[
            consumption.stock_lot_id
        ] = (
            quantities_by_lot.get(
                consumption.stock_lot_id,
                Decimal("0"),
            )
            + Decimal(consumption.quantity)
        )

    lot_ids = sorted(quantities_by_lot)

    lots_result = await db.execute(
        select(StockLot)
        .where(
            StockLot.company_id
            == document.company_id,
            StockLot.id.in_(lot_ids),
        )
        .order_by(StockLot.id)
        .with_for_update()
    )

    stock_lots = lots_result.scalars().all()

    lots_by_id = {
        stock_lot.id: stock_lot
        for stock_lot in stock_lots
    }

    if len(lots_by_id) != len(lot_ids):
        raise FIFOInventoryError(
            "One or more FIFO stock lots "
            "could not be found"
        )

    # Validate everything before changing any lot.
    for stock_lot_id, quantity in (
        quantities_by_lot.items()
    ):
        stock_lot = lots_by_id[
            stock_lot_id
        ]

        new_remaining_quantity = (
            Decimal(stock_lot.remaining_quantity)
            + quantity
        )

        if (
            new_remaining_quantity
            > Decimal(stock_lot.original_quantity)
        ):
            raise FIFOInventoryError(
                f"Cannot reverse issue because "
                f"FIFO lot {stock_lot.id} would exceed "
                f"its original quantity"
            )

    # Restore quantities to the exact lots
    # from which the issue consumed them.
    for stock_lot_id, quantity in (
        quantities_by_lot.items()
    ):
        stock_lot = lots_by_id[
            stock_lot_id
        ]

        stock_lot.remaining_quantity = (
            Decimal(stock_lot.remaining_quantity)
            + quantity
        )
        