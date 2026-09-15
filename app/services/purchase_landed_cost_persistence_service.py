"""Immutable receipt-level landed costs; caller owns commit and rollback.

This foundation records sources and allocations only. Inventory valuation,
journals and VAT are separate lifecycle integrations. Each create call records
a new cost; callers must not retry it as an idempotent request.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.document import Document, DocumentStatus, DocumentType
from app.models.document_line import DocumentLine
from app.models.purchase_landed_cost_allocation_event import PurchaseLandedCostAllocationEvent
from app.models.purchase_landed_cost_event import PurchaseLandedCostEvent
from app.models.trade_document import TradeDocument
from app.models.trade_fulfillment import TradeFulfillment
from app.models.trade_fulfillment_line import TradeFulfillmentLine
from app.services.purchase_landed_cost_allocation_calculation_service import (
    PurchaseLandedCostAllocationInput,
    calculate_purchase_landed_cost_allocations,
)
from app.services.trade_document_types import TradeDirection, TradeDocumentKind, TradeDocumentStatus


class PurchaseLandedCostPersistenceError(Exception):
    """Invalid landed-cost request or inconsistent persisted provenance."""


class PurchaseLandedCostSourceNotFoundError(PurchaseLandedCostPersistenceError):
    """Source does not exist in the requested company."""


@dataclass(frozen=True)
class PurchaseLandedCostPersistenceResult:
    event: PurchaseLandedCostEvent
    allocations: tuple[PurchaseLandedCostAllocationEvent, ...]
    created: bool


def _positive_id(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PurchaseLandedCostPersistenceError(f"{field} must be a positive integer")


def _business_date(value: date, field: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PurchaseLandedCostPersistenceError(f"{field} must be a date")


def _amount(value: Decimal) -> Decimal:
    try:
        if isinstance(value, (bool, float)):
            raise ValueError
        amount = Decimal(value)
        if not amount.is_finite() or not 0 < amount < Decimal("1e16"):
            raise ValueError
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PurchaseLandedCostPersistenceError(
            "amount must be positive, finite and fit Numeric(18, 2) exactly"
        ) from exc
    return amount


def _reason(value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > 50
    ):
        raise PurchaseLandedCostPersistenceError("reason_code must contain 1 to 50 characters")


async def _one(db: AsyncSession, statement):
    # Refresh identities after acquiring locks, including objects loaded earlier
    # in a caller-owned transaction with expire_on_commit=False.
    value = (await db.execute(
        statement.execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if value is None:
        raise PurchaseLandedCostSourceNotFoundError("Landed-cost source not found")
    return value


async def _receipt_lines(
    db: AsyncSession, *, company_id: int, trade_document_id: int,
    warehouse_document_id: int, currency_code: str, cost_date: date,
) -> tuple[TradeFulfillmentLine, ...]:
    # Match the purchase fulfillment reversal lock order: order, fulfillment,
    # mappings, warehouse document. The document lock also serializes the
    # active-cost guard in the warehouse reversal path.
    order = await _one(db, select(TradeDocument).where(
        TradeDocument.company_id == company_id,
        TradeDocument.id == trade_document_id,
    ).with_for_update())
    if (order.direction != TradeDirection.PURCHASE
            or order.kind != TradeDocumentKind.ORDER
            or order.status not in (TradeDocumentStatus.PARTIALLY_FULFILLED,
                                    TradeDocumentStatus.FULFILLED)):
        raise PurchaseLandedCostPersistenceError("Source must be a fulfilled purchase order")
    if order.currency_code != currency_code:
        raise PurchaseLandedCostPersistenceError("Landed cost currency must match purchase currency")

    fulfillment = await _one(db, select(TradeFulfillment).where(
        TradeFulfillment.company_id == company_id,
        TradeFulfillment.trade_document_id == trade_document_id,
        TradeFulfillment.warehouse_document_id == warehouse_document_id,
    ).with_for_update())
    if fulfillment.warehouse_document_type != DocumentType.RECEIPT.value:
        raise PurchaseLandedCostPersistenceError("Fulfillment must target a receipt")

    lines = tuple((await db.execute(select(TradeFulfillmentLine).where(
        TradeFulfillmentLine.company_id == company_id,
        TradeFulfillmentLine.fulfillment_id == fulfillment.id,
    ).order_by(TradeFulfillmentLine.id).with_for_update()
        .execution_options(populate_existing=True))).scalars().all())
    receipt = await _one(db, select(Document).where(
        Document.company_id == company_id, Document.id == warehouse_document_id,
    ).with_for_update())
    if receipt.document_type != DocumentType.RECEIPT or receipt.status != DocumentStatus.POSTED:
        raise PurchaseLandedCostPersistenceError("Landed cost requires a posted receipt")
    if cost_date < receipt.document_date:
        raise PurchaseLandedCostPersistenceError("cost_date cannot precede receipt date")
    warehouse_lines = {line.id: line for line in (await db.execute(
        select(DocumentLine).where(DocumentLine.document_id == receipt.id)
        .order_by(DocumentLine.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().all()}
    if not lines or {line.warehouse_document_line_id for line in lines} != set(warehouse_lines):
        raise PurchaseLandedCostPersistenceError("Receipt must have complete fulfillment mappings")
    for line in lines:
        target = warehouse_lines[line.warehouse_document_line_id]
        if (not line.quantity.is_finite() or line.quantity <= 0
                or line.trade_document_id != order.id
                or line.warehouse_document_id != receipt.id
                or line.product_id != target.product_id
                or line.warehouse_id != target.warehouse_id
                or line.quantity != target.quantity):
            raise PurchaseLandedCostPersistenceError("Receipt fulfillment provenance is inconsistent")
    return lines


async def create_purchase_landed_cost(
    db: AsyncSession, *, company_id: int, trade_document_id: int,
    warehouse_document_id: int, amount: Decimal, currency_code: str,
    cost_date: date, created_by: int, reason_code: str | None = None,
) -> PurchaseLandedCostPersistenceResult:
    """Create one cost and allocate it across the entire receipt by quantity.

    Identical calls intentionally represent distinct expenses. A request-level
    idempotency key must be added before exposing automatic API retries.
    """
    for field, value in (("company_id", company_id), ("trade_document_id", trade_document_id),
                         ("warehouse_document_id", warehouse_document_id), ("created_by", created_by)):
        _positive_id(value, field)
    amount = _amount(amount)
    _business_date(cost_date, "cost_date")
    _reason(reason_code)
    if not isinstance(currency_code, str) or re.fullmatch(r"[A-Z]{3}", currency_code) is None:
        raise PurchaseLandedCostPersistenceError("currency_code must contain three uppercase letters")
    lines = await _receipt_lines(
        db, company_id=company_id, trade_document_id=trade_document_id,
        warehouse_document_id=warehouse_document_id, currency_code=currency_code, cost_date=cost_date,
    )
    targets = calculate_purchase_landed_cost_allocations(
        total_amount=amount, lines=tuple(
            PurchaseLandedCostAllocationInput(line.id, line.quantity) for line in lines
        ),
    )
    event = PurchaseLandedCostEvent(
        company_id=company_id, trade_document_id=trade_document_id,
        warehouse_document_id=warehouse_document_id, amount=amount,
        currency_code=currency_code, cost_date=cost_date, created_by=created_by, reason_code=reason_code,
    )
    db.add(event)
    await db.flush()
    allocations = tuple(PurchaseLandedCostAllocationEvent(
        company_id=company_id, landed_cost_event_id=event.id,
        trade_fulfillment_line_id=line.id, trade_document_id=line.trade_document_id,
        trade_document_line_id=line.trade_document_line_id,
        warehouse_document_line_id=line.warehouse_document_line_id,
        product_id=line.product_id, warehouse_id=line.warehouse_id,
        quantity=target.quantity, allocated_amount=target.allocated_amount,
    ) for line, target in zip(lines, targets, strict=True))
    db.add_all(allocations)
    await db.flush()
    return PurchaseLandedCostPersistenceResult(event, allocations, True)


_ALLOCATION_FIELDS = (
    "trade_fulfillment_line_id", "trade_document_id", "trade_document_line_id",
    "warehouse_document_line_id", "product_id", "warehouse_id", "quantity", "allocated_amount",
)


async def _allocations(db: AsyncSession, event: PurchaseLandedCostEvent):
    rows = tuple((await db.execute(select(PurchaseLandedCostAllocationEvent).where(
        PurchaseLandedCostAllocationEvent.company_id == event.company_id,
        PurchaseLandedCostAllocationEvent.landed_cost_event_id == event.id,
    ).order_by(PurchaseLandedCostAllocationEvent.trade_fulfillment_line_id)
        .with_for_update().execution_options(populate_existing=True))).scalars().all())
    if (not rows or not event.amount.is_finite() or event.amount <= 0
            or any(not row.allocated_amount.is_finite() or not row.quantity.is_finite()
                   or row.allocated_amount <= 0 or row.quantity <= 0
                   or row.trade_document_id != event.trade_document_id for row in rows)
            or sum(row.allocated_amount for row in rows) != event.amount):
        raise PurchaseLandedCostPersistenceError("Persisted allocations do not reconcile to source")
    return rows


async def reverse_purchase_landed_cost(
    db: AsyncSession, *, company_id: int, landed_cost_event_id: int,
    reversal_date: date, reversed_by: int, reason_code: str | None = None,
) -> PurchaseLandedCostPersistenceResult:
    """Append a reversal with exact historical allocations; never recalculate.

    Repeating an identical reversal returns the existing event. A different
    date, author or reason conflicts. Positive reversal amounts are interpreted
    with a negative economic sign through reversal_of_id by downstream readers.
    """
    for field, value in (("company_id", company_id), ("landed_cost_event_id", landed_cost_event_id),
                         ("reversed_by", reversed_by)):
        _positive_id(value, field)
    _business_date(reversal_date, "reversal_date")
    _reason(reason_code)
    source = await _one(db, select(PurchaseLandedCostEvent).where(
        PurchaseLandedCostEvent.company_id == company_id,
        PurchaseLandedCostEvent.id == landed_cost_event_id,
    ).with_for_update())
    if source.reversal_of_id is not None:
        raise PurchaseLandedCostPersistenceError("Cannot reverse a reversal event")
    if reversal_date < source.cost_date:
        raise PurchaseLandedCostPersistenceError("reversal_date cannot precede cost_date")
    originals = await _allocations(db, source)
    existing = (await db.execute(select(PurchaseLandedCostEvent).where(
        PurchaseLandedCostEvent.company_id == company_id,
        PurchaseLandedCostEvent.reversal_of_id == source.id,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if existing is not None:
        rows = await _allocations(db, existing)
        if (existing.cost_date != reversal_date or existing.created_by != reversed_by
                or existing.reason_code != reason_code or existing.amount != source.amount
                or existing.currency_code != source.currency_code
                or existing.trade_document_id != source.trade_document_id
                or existing.warehouse_document_id != source.warehouse_document_id
                or [tuple(getattr(r, f) for f in _ALLOCATION_FIELDS) for r in rows]
                != [tuple(getattr(r, f) for f in _ALLOCATION_FIELDS) for r in originals]):
            raise PurchaseLandedCostPersistenceError("Existing reversal conflicts with requested reversal")
        return PurchaseLandedCostPersistenceResult(existing, rows, False)

    reversal = PurchaseLandedCostEvent(
        company_id=company_id, trade_document_id=source.trade_document_id,
        warehouse_document_id=source.warehouse_document_id, amount=source.amount,
        currency_code=source.currency_code, cost_date=reversal_date,
        created_by=reversed_by, reason_code=reason_code, reversal_of_id=source.id,
    )
    db.add(reversal)
    await db.flush()
    rows = tuple(PurchaseLandedCostAllocationEvent(
        company_id=company_id, landed_cost_event_id=reversal.id,
        **{field: getattr(row, field) for field in _ALLOCATION_FIELDS},
    ) for row in originals)
    db.add_all(rows)
    await db.flush()
    return PurchaseLandedCostPersistenceResult(reversal, rows, True)


async def has_active_purchase_landed_costs(
    db: AsyncSession, *, company_id: int, warehouse_document_id: int,
) -> bool:
    """Caller must lock the receipt before using this as a reversal guard."""
    reversal = aliased(PurchaseLandedCostEvent)
    return (await db.execute(select(PurchaseLandedCostEvent.id).where(
        PurchaseLandedCostEvent.company_id == company_id,
        PurchaseLandedCostEvent.warehouse_document_id == warehouse_document_id,
        PurchaseLandedCostEvent.reversal_of_id.is_(None),
        ~select(reversal.id).where(
            reversal.company_id == company_id,
            reversal.reversal_of_id == PurchaseLandedCostEvent.id,
        ).exists(),
    ).limit(1))).scalar_one_or_none() is not None
