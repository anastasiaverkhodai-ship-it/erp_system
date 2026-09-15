from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.sales_recognition_event import SalesRecognitionEvent
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.models.trade_fulfillment_line import TradeFulfillmentLine
from app.services.sales_profitability_calculation_service import (
    SalesGrossProfitabilityProjection,
    calculate_sales_gross_profitability,
)


ZERO = Decimal("0")


@dataclass(frozen=True)
class SalesProfitabilityEconomicSource:
    sales_recognition_event_id: int
    invoice_fulfillment_allocation_id: int
    fulfillment_line_id: int
    warehouse_document_line_id: int
    inventory_cost_entry_id: int
    recognized_gross_amount: Decimal
    recognized_tax_amount: Decimal
    returned_net_revenue_amount: Decimal
    inventory_cost_amount: Decimal
    restored_cost_amount: Decimal


@dataclass(frozen=True)
class SalesProfitabilityProjectionResult:
    source: SalesProfitabilityEconomicSource
    profitability: SalesGrossProfitabilityProjection


def _decimal(value: Decimal | int | str | None) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _active_sales_recognition_predicate():
    reversal = aliased(
        SalesRecognitionEvent,
        name="sales_recognition_reversal",
    )

    return (
        SalesRecognitionEvent.reversal_of_id.is_(None)
        & ~select(reversal.id)
        .where(
            reversal.company_id
            == SalesRecognitionEvent.company_id,
            reversal.reversal_of_id
            == SalesRecognitionEvent.id,
        )
        .correlate(SalesRecognitionEvent)
        .exists()
    )


def _active_return_recognition_predicate():
    reversal = aliased(
        SalesReturnRecognitionEvent,
        name="sales_return_recognition_reversal",
    )

    return (
        SalesReturnRecognitionEvent.reversal_of_id.is_(None)
        & ~select(reversal.id)
        .where(
            reversal.company_id
            == SalesReturnRecognitionEvent.company_id,
            reversal.reversal_of_id
            == SalesReturnRecognitionEvent.id,
        )
        .correlate(SalesReturnRecognitionEvent)
        .exists()
    )


def _active_cost_restoration_predicate():
    reversal = aliased(
        SalesReturnCostRestorationEvent,
        name="sales_return_cost_restoration_reversal",
    )

    return (
        SalesReturnCostRestorationEvent.reversal_of_id.is_(None)
        & ~select(reversal.id)
        .where(
            reversal.company_id
            == SalesReturnCostRestorationEvent.company_id,
            reversal.reversal_of_id
            == SalesReturnCostRestorationEvent.id,
        )
        .correlate(SalesReturnCostRestorationEvent)
        .exists()
    )


async def load_sales_profitability_projection(
    session: AsyncSession,
    *,
    company_id: int,
    sales_recognition_event_id: int,
) -> SalesProfitabilityProjectionResult:
    """
    Load one sales-recognition profitability projection from canonical
    immutable economic snapshots.

    Provenance:
        SalesRecognitionEvent
        -> InvoiceFulfillmentAllocation
        -> TradeFulfillmentLine
        -> InventoryCostEntry

    Sales returns are applied only through their canonical immutable
    revenue-recognition and cost-restoration events.

    No persistence, commit, rollback, FX conversion, VAT inference,
    repricing, recosting, or GL mutation occurs here.
    """

    if company_id <= 0:
        raise ValueError("company_id must be greater than zero")

    if sales_recognition_event_id <= 0:
        raise ValueError(
            "sales_recognition_event_id must be greater than zero"
        )

    source_stmt = (
        select(
            SalesRecognitionEvent,
            InvoiceFulfillmentAllocation,
            TradeFulfillmentLine,
            InventoryCostEntry,
        )
        .join(
            InvoiceFulfillmentAllocation,
            (
                InvoiceFulfillmentAllocation.id
                == SalesRecognitionEvent
                .invoice_fulfillment_allocation_id
            ),
        )
        .join(
            TradeFulfillmentLine,
            (
                TradeFulfillmentLine.id
                == InvoiceFulfillmentAllocation.fulfillment_line_id
            ),
        )
        .join(
            InventoryCostEntry,
            (
                InventoryCostEntry.document_line_id
                == TradeFulfillmentLine.warehouse_document_line_id
            ),
        )
        .where(
            SalesRecognitionEvent.company_id == company_id,
            SalesRecognitionEvent.id == sales_recognition_event_id,
            _active_sales_recognition_predicate(),
            InvoiceFulfillmentAllocation.company_id == company_id,
            TradeFulfillmentLine.company_id == company_id,
            InventoryCostEntry.company_id == company_id,
        )
    )

    source_rows = list(
        (await session.execute(source_stmt)).all()
    )

    if not source_rows:
        raise ValueError(
            "active sales profitability economic source was not found"
        )

    if len(source_rows) != 1:
        raise ValueError(
            "sales profitability economic source is ambiguous"
        )

    (
        recognition,
        allocation,
        fulfillment_line,
        cost_entry,
    ) = source_rows[0]

    return_revenue_stmt = (
        select(SalesReturnRecognitionEvent)
        .where(
            SalesReturnRecognitionEvent.company_id == company_id,
            SalesReturnRecognitionEvent.sales_recognition_event_id
            == recognition.id,
            _active_return_recognition_predicate(),
        )
    )

    return_events = list(
        (await session.execute(return_revenue_stmt)).scalars().all()
    )

    returned_net_revenue = ZERO

    for event in return_events:
        returned_gross = _decimal(
            event.recognized_gross_amount
        )
        returned_tax = _decimal(
            event.recognized_tax_amount
        )

        if returned_tax > returned_gross:
            raise ValueError(
                "sales return recognized tax exceeds gross amount"
            )

        returned_net_revenue += (
            returned_gross - returned_tax
        )

    restored_cost_stmt = (
        select(SalesReturnCostRestorationEvent)
        .where(
            SalesReturnCostRestorationEvent.company_id
            == company_id,
            SalesReturnCostRestorationEvent.inventory_cost_entry_id
            == cost_entry.id,
            _active_cost_restoration_predicate(),
        )
    )

    cost_events = list(
        (await session.execute(restored_cost_stmt)).scalars().all()
    )

    restored_cost = sum(
        (
            _decimal(event.restored_cost_amount)
            for event in cost_events
        ),
        ZERO,
    )

    recognized_gross = _decimal(
        recognition.recognized_gross_amount
    )
    recognized_tax = _decimal(
        recognition.recognized_tax_amount
    )
    inventory_cost = _decimal(
        cost_entry.cost_amount
    )

    projection = calculate_sales_gross_profitability(
        recognized_gross_amount=recognized_gross,
        recognized_tax_amount=recognized_tax,
        returned_net_revenue_amount=returned_net_revenue,
        inventory_cost_amount=inventory_cost,
        restored_cost_amount=restored_cost,
    )

    return SalesProfitabilityProjectionResult(
        source=SalesProfitabilityEconomicSource(
            sales_recognition_event_id=recognition.id,
            invoice_fulfillment_allocation_id=allocation.id,
            fulfillment_line_id=fulfillment_line.id,
            warehouse_document_line_id=(
                fulfillment_line.warehouse_document_line_id
            ),
            inventory_cost_entry_id=cost_entry.id,
            recognized_gross_amount=recognized_gross,
            recognized_tax_amount=recognized_tax,
            returned_net_revenue_amount=returned_net_revenue,
            inventory_cost_amount=inventory_cost,
            restored_cost_amount=restored_cost,
        ),
        profitability=projection,
    )
