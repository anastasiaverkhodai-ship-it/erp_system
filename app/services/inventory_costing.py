from datetime import date
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import (
    Company,
    InventoryValuationMethod,
)
from app.models.document import (
    Document,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.models.inventory_cost_entry import InventoryCostEntry
from app.services.fifo_inventory import (
    FIFOInventoryError,
    calculate_fifo_cost,
    consume_fifo,
    create_stock_lot,
    reverse_issue_fifo,
    reverse_receipt_fifo,
)

from app.services.moving_average_inventory import (
    MovingAverageInventoryError,
    process_moving_average_issue,
    process_moving_average_receipt,
    reverse_moving_average_document,
)

class InventoryCostingError(Exception):
    """Business error raised by inventory costing operations."""


def _money(
    value: Decimal,
) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _unit_cost(
    value: Decimal,
) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_HALF_UP,
    )

def _valuation_amount(
    value: Decimal,
) -> Decimal:
    return Decimal(value).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_HALF_UP,
    )

async def get_inventory_valuation_method(
    db: AsyncSession,
    company_id: int,
) -> InventoryValuationMethod:
    result = await db.execute(
        select(
            Company.inventory_valuation_method
        ).where(
            Company.id == company_id,
            Company.is_active.is_(True),
        )
    )

    method = result.scalar_one_or_none()

    if method is None:
        raise InventoryCostingError(
            "Company not found or inactive"
        )

    return method


async def process_inventory_receipt(
    db: AsyncSession,
    document: Document,
    line: DocumentLine,
) -> None:
    method = await get_inventory_valuation_method(
        db=db,
        company_id=document.company_id,
    )

    if method == InventoryValuationMethod.FIFO:
        try:
            await create_stock_lot(
                db=db,
                document=document,
                line=line,
                quantity=line.quantity,
            )
        except FIFOInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

        return

    if (
        method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        try:
           await process_moving_average_receipt(
    db=db,
    company_id=document.company_id,
    document_id=document.id,
    document_line_id=line.id,
    product_id=line.product_id,
    warehouse_id=line.warehouse_id,
    movement_date=document.document_date,
    quantity=line.quantity,
    unit_cost=line.price,
)
        except MovingAverageInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

        return

    raise InventoryCostingError(
        f"Inventory valuation method "
        f"'{method.value}' is not implemented yet"
    )


async def process_inventory_issue(
    db: AsyncSession,
    document: Document,
    line: DocumentLine,
) -> InventoryCostEntry:
    method = await get_inventory_valuation_method(
        db=db,
        company_id=document.company_id,
    )

    if method == InventoryValuationMethod.FIFO:
        try:
            consumptions = await consume_fifo(
                db=db,
                document=document,
                line=line,
                quantity=line.quantity,
            )

            raw_cost = calculate_fifo_cost(
                consumptions
            )

        except FIFOInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

    elif (
        method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        try:
            raw_cost = await process_moving_average_issue(
    db=db,
    company_id=document.company_id,
    document_id=document.id,
    document_line_id=line.id,
    product_id=line.product_id,
    warehouse_id=line.warehouse_id,
    movement_date=document.document_date,
    quantity=line.quantity,
)

        except MovingAverageInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

    else:
        raise InventoryCostingError(
            f"Inventory valuation method "
            f"'{method.value}' is not implemented yet"
        )

    valuation_amount = _valuation_amount(
    raw_cost
)

    cost_amount = _money(
    valuation_amount
)

    effective_unit_cost = _unit_cost(
    valuation_amount
    / Decimal(line.quantity)
)

    cost_entry = InventoryCostEntry(
    company_id=document.company_id,
    document_id=document.id,
    document_line_id=line.id,
    valuation_method=method,
    quantity=line.quantity,
    unit_cost=effective_unit_cost,
    valuation_amount=valuation_amount,
    cost_amount=cost_amount,
)
    db.add(cost_entry)

    return cost_entry

async def reverse_inventory_costing(
    db: AsyncSession,
    document: Document,
    reversal_date: date,
) -> None:
    method = await get_inventory_valuation_method(
        db=db,
        company_id=document.company_id,
    )

    if method == InventoryValuationMethod.FIFO:
        try:
            if (
                document.document_type
                == DocumentType.RECEIPT
            ):
                await reverse_receipt_fifo(
                    db=db,
                    document=document,
                )

            elif (
                document.document_type
                == DocumentType.ISSUE
            ):
                await reverse_issue_fifo(
                    db=db,
                    document=document,
                )

            else:
                return

        except FIFOInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

        return

    if (
        method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        try:
            await reverse_moving_average_document(
                db=db,
                company_id=document.company_id,
                document_id=document.id,
                reversal_date=reversal_date,
            )

        except MovingAverageInventoryError as exc:
            raise InventoryCostingError(
                str(exc)
            ) from exc

        return

    raise InventoryCostingError(
        f"Inventory valuation method "
        f"'{method.value}' is not implemented yet"
    )