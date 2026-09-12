from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import InventoryValuationMethod
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.stock_lot_consumption import (
    StockLotConsumption,
)
from app.services.warehouse_transfer_valuation_calculation_service import (
    WarehouseTransferFifoConsumptionSlice,
    WarehouseTransferSourceInventoryCost,
    WarehouseTransferValuationResult,
    calculate_warehouse_transfer_valuation,
)


class WarehouseTransferSourceValuationLoaderError(
    ValueError
):
    pass


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise WarehouseTransferSourceValuationLoaderError(
            f"{field} must be a positive integer"
        )

    return value


async def load_warehouse_transfer_source_valuation(
    db: AsyncSession,
    *,
    company_id: int,
    issue_document_id: int,
    issue_document_line_id: int,
) -> WarehouseTransferValuationResult:
    """
    Load immutable source ISSUE valuation provenance and convert it
    into the pure transfer valuation result.

    Read-only.

    Source truth:
      InventoryCostEntry
        - one exact aggregate valuation per ISSUE DocumentLine.

    FIFO:
      StockLotConsumption rows
        - one or many exact historical cost slices.

    Moving average:
      no StockLotConsumption rows are allowed/required;
      source ICE itself is exact valuation truth.

    No commit / rollback.
    No ORM mutation.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    issue_document_id = _positive_id(
        issue_document_id,
        field="issue_document_id",
    )

    issue_document_line_id = _positive_id(
        issue_document_line_id,
        field="issue_document_line_id",
    )

    ice_result = await db.execute(
        select(
            InventoryCostEntry
        ).where(
            InventoryCostEntry.company_id
            == company_id,
            InventoryCostEntry.document_id
            == issue_document_id,
            InventoryCostEntry.document_line_id
            == issue_document_line_id,
        )
    )

    ice = ice_result.scalar_one_or_none()

    if ice is None:
        raise WarehouseTransferSourceValuationLoaderError(
            "source InventoryCostEntry was not found"
        )

    source = WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=ice.id,
        valuation_method=ice.valuation_method,
        quantity=ice.quantity,
        unit_cost=ice.unit_cost,
        valuation_amount=ice.valuation_amount,
    )

    if (
        ice.valuation_method
        == InventoryValuationMethod.FIFO
    ):
        consumptions_result = await db.execute(
            select(
                StockLotConsumption
            )
            .where(
                StockLotConsumption.company_id
                == company_id,
                StockLotConsumption.issue_document_id
                == issue_document_id,
                StockLotConsumption.issue_document_line_id
                == issue_document_line_id,
            )
            .order_by(
                StockLotConsumption.id
            )
        )

        consumptions = tuple(
            consumptions_result.scalars().all()
        )

        if not consumptions:
            raise WarehouseTransferSourceValuationLoaderError(
                "FIFO source ISSUE has no "
                "StockLotConsumption provenance"
            )

        fifo_slices = tuple(
            WarehouseTransferFifoConsumptionSlice(
                stock_lot_consumption_id=row.id,
                quantity=row.quantity,
                unit_cost=row.unit_cost,
            )
            for row in consumptions
        )

    elif (
        ice.valuation_method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        # MA source truth is the immutable ICE itself.
        # Do not manufacture FIFO provenance.
        fifo_slices = ()

    else:
        raise WarehouseTransferSourceValuationLoaderError(
            "unsupported source inventory valuation method"
        )

    try:
        return calculate_warehouse_transfer_valuation(
            source=source,
            fifo_consumptions=fifo_slices,
        )
    except Exception as exc:
        # Preserve a loader-level boundary for lifecycle callers while
        # keeping the pure calculator independent from persistence.
        from app.services.warehouse_transfer_valuation_calculation_service import (
            WarehouseTransferValuationCalculationError,
        )

        if isinstance(
            exc,
            WarehouseTransferValuationCalculationError,
        ):
            raise WarehouseTransferSourceValuationLoaderError(
                str(exc)
            ) from exc

        raise
