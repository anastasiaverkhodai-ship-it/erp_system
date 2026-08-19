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
        Decimal("0.0001"),
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

        cost_amount = _money(
            raw_cost
        )

        effective_unit_cost = _unit_cost(
            raw_cost / Decimal(line.quantity)
        )

        cost_entry = InventoryCostEntry(
            company_id=document.company_id,
            document_id=document.id,
            document_line_id=line.id,
            valuation_method=method,
            quantity=line.quantity,
            unit_cost=effective_unit_cost,
            cost_amount=cost_amount,
        )

        db.add(cost_entry)

        return cost_entry

    raise InventoryCostingError(
        f"Inventory valuation method "
        f"'{method.value}' is not implemented yet"
    )

async def reverse_inventory_costing(
    db: AsyncSession,
    document: Document,
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

    raise InventoryCostingError(
        f"Inventory valuation method "
        f"'{method.value}' is not implemented yet"
    )