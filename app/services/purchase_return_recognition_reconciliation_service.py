from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import (
    and_,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import (
    DocumentLine,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.purchase_return_recognition_calculation_service import (
    PurchaseReturnEconomicCapacity,
    PurchaseReturnRecognitionCalculationError,
    PurchaseReturnRecognitionTarget,
    build_purchase_return_recognition_targets,
)
from app.services.purchase_return_recognition_persistence_service import (
    PurchaseReturnRecognitionPersistenceError,
    reconcile_purchase_return_recognition_source,
)
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionCalculationError,
    SalesRecognitionCandidate,
    build_sales_recognition_targets,
)
from app.services.supplier_economic_liability_calculation_service import (
    SupplierEconomicLiabilityCalculationError,
    SupplierReceiptBaseAllocationCandidate,
    build_supplier_receipt_base_allocation_targets,
)
from app.services.tax_types import (
    TaxDirection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_return_calculation_service import (
    TradeReturnCandidate,
)


ZERO = Decimal("0")


class PurchaseReturnRecognitionReconciliationError(
    Exception
):
    """Base Purchase Return economic reconciliation error."""


class PurchaseReturnRecognitionReconciliationDataIntegrityError(
    PurchaseReturnRecognitionReconciliationError
):
    """Persistent Purchase Return source state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInvoiceLineSnapshot:
    """
    Immutable PURCHASE Invoice commercial truth.

    VAT-configured line:
        gross =
            TaxCalculation.taxable_base
            +
            TaxCalculation.tax_amount

    Non-VAT line:
        gross =
            TradeDocumentLine.quantity
            *
            TradeDocumentLine.unit_price

    tax_amount is a snapshot only. It does not authorize INPUT VAT,
    TaxRecognitionEvent changes, TaxCreditEvidence changes, or RK.
    """

    invoice_id: int
    invoice_line_id: int
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnReceiptPeerSnapshot:
    """
    One ACTIVE InvoiceFulfillmentAllocation consuming one persisted
    POSTED warehouse RECEIPT line.

    receipt_price is the already-persisted VAT-exclusive warehouse
    accounting price. It is authoritative for receipt-base allocation.
    """

    source_id: int
    receipt_document_id: int
    receipt_line_id: int
    event_date: date
    receipt_quantity: Decimal
    receipt_price: Decimal
    allocation_quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnCommercialComponent:
    source_id: int
    event_date: date
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnRecognitionReconciliationResult:
    fulfillment_id: int
    fulfillment_line_id: int
    currency_code: str | None

    return_candidates: tuple[
        TradeReturnCandidate,
        ...,
    ]

    capacities: tuple[
        PurchaseReturnEconomicCapacity,
        ...,
    ]

    desired_targets: tuple[
        PurchaseReturnRecognitionTarget,
        ...,
    ]

    current_pair_keys: tuple[
        tuple[int, int],
        ...,
    ]

    created_events: tuple[
        PurchaseReturnRecognitionEvent,
        ...,
    ]


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(
            value
        )
    )


def _positive_int(
    value: int,
    *,
    label: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                f"{label} must be greater than zero"
            )
        )

    return value


def _currency(
    value: str,
) -> str:
    code = str(
        value
    ).strip().upper()

    if (
        len(
            code
        )
        != 3
        or not code.isalpha()
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "currency_code must contain exactly "
                "three alphabetic characters"
            )
        )

    return code


def _active_original_rows(
    rows: Iterable,
    *,
    label: str,
):
    """
    Rebuild active immutable originals.

    Original:
        reversal_of_id IS NULL

    Reversal:
        reversal_of_id -> one original row.

    Historical rows are never deleted or updated.
    """

    rows = tuple(
        rows
    )

    by_id = {}
    originals = []
    reversed_original_ids = set()

    for row in rows:
        row_id = _positive_int(
            row.id,
            label=f"{label} id",
        )

        if row_id in by_id:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    f"Duplicate {label} id"
                )
            )

        by_id[row_id] = row

        if row.reversal_of_id is None:
            originals.append(
                row
            )
            continue

        reversal_of_id = _positive_int(
            row.reversal_of_id,
            label=f"{label} reversal_of_id",
        )

        if reversal_of_id in reversed_original_ids:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    f"{label} original has more than one reversal"
                )
            )

        reversed_original_ids.add(
            reversal_of_id
        )

    original_ids = {
        row.id
        for row in originals
    }

    unknown = (
        reversed_original_ids
        - original_ids
    )

    if unknown:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                f"{label} reversal references "
                "a non-original history row"
            )
        )

    return tuple(
        row
        for row in originals
        if row.id
        not in reversed_original_ids
    )


def build_purchase_return_invoice_line_snapshot(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
    calculation: TaxCalculation | None,
) -> PurchaseReturnInvoiceLineSnapshot:
    """
    Build immutable commercial PURCHASE Invoice truth.

    Warehouse receipt prices are deliberately absent.
    """

    if document.id is None:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Persistent Purchase Invoice ID is required"
            )
        )

    if line.id is None:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Persistent Purchase Invoice line ID is required"
            )
        )

    if (
        document.direction
        != TradeDirection.PURCHASE
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Return recognition requires "
                "PURCHASE invoice"
            )
        )

    if (
        document.kind
        != TradeDocumentKind.INVOICE
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Return recognition requires "
                "Trade Invoice"
            )
        )

    if (
        document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Return recognition requires "
                "confirmed Purchase Invoice"
            )
        )

    if (
        line.company_id
        != document.company_id
        or line.trade_document_id
        != document.id
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice line source mismatch"
            )
        )

    quantity = _decimal(
        line.quantity
    )

    if quantity <= ZERO:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice line quantity "
                "must be greater than zero"
            )
        )

    currency_code = _currency(
        document.currency_code
    )

    has_tax_configuration = (
        line.tax_rate_code
        is not None
    )

    if has_tax_configuration:
        if calculation is None:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "VAT-configured confirmed Purchase Invoice "
                    "line has no immutable TaxCalculation"
                )
            )

        if (
            calculation.company_id
            != document.company_id
            or calculation.trade_document_id
            != document.id
            or calculation.trade_document_line_id
            != line.id
            or calculation.product_id
            != line.product_id
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "TaxCalculation Purchase Invoice "
                    "source mismatch"
                )
            )

        if (
            calculation.direction
            != TaxDirection.INPUT
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Invoice TaxCalculation "
                    "must be INPUT"
                )
            )

        if (
            _currency(
                calculation.currency_code
            )
            != currency_code
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Invoice and TaxCalculation "
                    "currency mismatch"
                )
            )

        taxable_base = _decimal(
            calculation.taxable_base
        )

        tax_amount = _decimal(
            calculation.tax_amount
        )

        if taxable_base < ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Invoice taxable base "
                    "cannot be negative"
                )
            )

        if tax_amount < ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Invoice tax amount "
                    "cannot be negative"
                )
            )

        gross_amount = (
            round_currency_amount(
                amount=(
                    taxable_base
                    + tax_amount
                ),
                currency_code=currency_code,
            )
        )

        tax_amount = (
            round_currency_amount(
                amount=tax_amount,
                currency_code=currency_code,
            )
        )

    else:
        if calculation is not None:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Non-VAT Purchase Invoice line "
                    "unexpectedly has TaxCalculation"
                )
            )

        gross_amount = (
            round_currency_amount(
                amount=(
                    quantity
                    * _decimal(
                        line.unit_price
                    )
                ),
                currency_code=currency_code,
            )
        )

        tax_amount = (
            round_currency_amount(
                amount=ZERO,
                currency_code=currency_code,
            )
        )

    if gross_amount <= ZERO:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice line gross amount "
                "must be greater than zero"
            )
        )

    if (
        tax_amount < ZERO
        or tax_amount > gross_amount
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice tax amount is invalid"
            )
        )

    return PurchaseReturnInvoiceLineSnapshot(
        invoice_id=document.id,
        invoice_line_id=line.id,
        quantity=quantity,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


async def _load_trade_return_history(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    TradeReturnEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TradeReturnEvent
                )
                .where(
                    (
                        TradeReturnEvent.company_id
                        == company_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                    (
                        TradeReturnEvent.direction
                        == "purchase"
                    ),
                )
                .order_by(
                    TradeReturnEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _build_active_return_candidates(
    *,
    history: Iterable[
        TradeReturnEvent
    ],
) -> tuple[
    TradeReturnCandidate,
    ...,
]:
    active = _active_original_rows(
        history,
        label="TradeReturnEvent",
    )

    candidates = []

    for event in active:
        if str(
            event.direction
        ).lower() != "purchase":
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Return reconciliation contains "
                    "non-PURCHASE TradeReturnEvent"
                )
            )

        quantity = _decimal(
            event.returned_quantity
        )

        if quantity <= ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Active TradeReturnEvent quantity "
                    "must be greater than zero"
                )
            )

        if not isinstance(
            event.return_date,
            date,
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Active TradeReturnEvent return_date "
                    "must be a date"
                )
            )

        candidates.append(
            TradeReturnCandidate(
                source_id=_positive_int(
                    event.id,
                    label="TradeReturnEvent id",
                ),
                event_date=event.return_date,
                quantity=quantity,
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.event_date,
                candidate.source_id,
            ),
        )
    )


async def _discover_target_allocations(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    InvoiceFulfillmentAllocation,
    ...,
]:
    """
    Initial discovery only.

    Stable monetary reconciliation later locks each immutable
    Purchase Invoice line and then reloads all ACTIVE peer allocations.
    """

    return tuple(
        (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation
                )
                .where(
                    (
                        InvoiceFulfillmentAllocation
                        .company_id
                        == company_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .fulfillment_line_id
                        == fulfillment_line_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .status
                        == (
                            InvoiceFulfillmentAllocationStatus
                            .ACTIVE
                        )
                    ),
                )
                .order_by(
                    InvoiceFulfillmentAllocation.invoice_id,
                    InvoiceFulfillmentAllocation.invoice_line_id,
                    InvoiceFulfillmentAllocation.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def _lock_purchase_invoice_line_context(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
) -> tuple[
    TradeDocument,
    TradeDocumentLine,
    TaxCalculation | None,
]:
    line = (
        await db.execute(
            select(
                TradeDocumentLine
            )
            .where(
                (
                    TradeDocumentLine.company_id
                    == company_id
                ),
                (
                    TradeDocumentLine.trade_document_id
                    == invoice_id
                ),
                (
                    TradeDocumentLine.id
                    == invoice_line_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice line was not found"
            )
        )

    document = (
        await db.execute(
            select(
                TradeDocument
            )
            .where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if document is None:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice was not found"
            )
        )

    calculation = (
        await db.execute(
            select(
                TaxCalculation
            )
            .where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.trade_document_id
                == invoice_id,
                TaxCalculation.trade_document_line_id
                == invoice_line_id,
            )
        )
    ).scalar_one_or_none()

    return (
        document,
        line,
        calculation,
    )


async def _load_invoice_commercial_components(
    db: AsyncSession,
    *,
    company_id: int,
    snapshot: PurchaseReturnInvoiceLineSnapshot,
) -> tuple[
    dict[int, InvoiceFulfillmentAllocation],
    dict[int, PurchaseReturnCommercialComponent],
]:
    """
    Rebuild gross/tax monetary slices across ALL ACTIVE allocations of
    one immutable Purchase Invoice line.

    The existing SalesRecognition pure calculator is intentionally
    reused here only as the neutral cumulative-delta commercial
    allocator. It does not post Sales accounting.
    """

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                TradeFulfillmentLine,
                Document,
            )
            .join(
                TradeFulfillmentLine,
                and_(
                    (
                        TradeFulfillmentLine.company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.fulfillment_id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_line_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.trade_document_id
                        == (
                            InvoiceFulfillmentAllocation
                            .order_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.trade_document_line_id
                        == (
                            InvoiceFulfillmentAllocation
                            .order_line_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.product_id
                        == (
                            InvoiceFulfillmentAllocation
                            .product_id
                        )
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    (
                        Document.company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        Document.id
                        == TradeFulfillmentLine.warehouse_document_id
                    ),
                ),
            )
            .where(
                (
                    InvoiceFulfillmentAllocation.company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation.invoice_id
                    == snapshot.invoice_id
                ),
                (
                    InvoiceFulfillmentAllocation.invoice_line_id
                    == snapshot.invoice_line_id
                ),
                (
                    InvoiceFulfillmentAllocation.status
                    == (
                        InvoiceFulfillmentAllocationStatus
                        .ACTIVE
                    )
                ),
            )
            .order_by(
                Document.document_date,
                InvoiceFulfillmentAllocation.id,
            )
            .with_for_update()
        )
    ).all()

    allocations = {}
    candidates = []

    for (
        allocation,
        fulfillment_line,
        receipt_document,
    ) in rows:
        if (
            receipt_document.status
            != DocumentStatus.POSTED
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "ACTIVE Purchase Invoice allocation must "
                    "reference POSTED warehouse document"
                )
            )

        if (
            receipt_document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase economic capacity requires "
                    "warehouse RECEIPT"
                )
            )

        quantity = _decimal(
            allocation.quantity
        )

        if quantity <= ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "ACTIVE InvoiceFulfillmentAllocation "
                    "quantity must be positive"
                )
            )

        if allocation.id in allocations:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Duplicate ACTIVE InvoiceFulfillmentAllocation"
                )
            )

        allocations[
            allocation.id
        ] = allocation

        candidates.append(
            SalesRecognitionCandidate(
                source_id=allocation.id,
                event_date=(
                    receipt_document.document_date
                ),
                quantity=quantity,
            )
        )

    try:
        targets = (
            build_sales_recognition_targets(
                invoice_line_quantity=(
                    snapshot.quantity
                ),
                invoice_line_gross_amount=(
                    snapshot.gross_amount
                ),
                invoice_line_tax_amount=(
                    snapshot.tax_amount
                ),
                currency_code=(
                    snapshot.currency_code
                ),
                candidates=candidates,
            )
        )
    except (
        SalesRecognitionCalculationError
    ) as exc:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Invoice commercial allocation failed: "
                f"{exc}"
            )
        ) from exc

    components = {}

    for target in targets:
        allocation = allocations.get(
            target.source_id
        )

        if allocation is None:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Commercial allocation target has no "
                    "ACTIVE InvoiceFulfillmentAllocation"
                )
            )

        components[
            target.source_id
        ] = PurchaseReturnCommercialComponent(
            source_id=target.source_id,
            event_date=target.event_date,
            quantity=_decimal(
                target.quantity
            ),
            gross_amount=_decimal(
                target.gross_amount
            ),
            tax_amount=_decimal(
                target.tax_amount
            ),
            currency_code=snapshot.currency_code,
        )

    return (
        allocations,
        components,
    )


async def _load_receipt_peer_snapshots(
    db: AsyncSession,
    *,
    company_id: int,
    target_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
) -> tuple[
    PurchaseReturnReceiptPeerSnapshot,
    ...,
]:
    """
    Load ALL ACTIVE peers for every receipt/fulfillment line consumed
    by the target allocations.

    This is required before cumulative receipt-base rounding.
    """

    if not target_allocations:
        return ()

    peer_keys = tuple(
        sorted(
            {
                (
                    allocation.fulfillment_id,
                    allocation.fulfillment_line_id,
                )
                for allocation
                in target_allocations
            }
        )
    )

    peer_filter = or_(
        *[
            and_(
                (
                    InvoiceFulfillmentAllocation
                    .fulfillment_id
                    == fulfillment_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .fulfillment_line_id
                    == fulfillment_line_id
                ),
            )
            for (
                fulfillment_id,
                fulfillment_line_id,
            )
            in peer_keys
        ]
    )

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                TradeFulfillmentLine,
                DocumentLine,
                Document,
            )
            .join(
                TradeFulfillmentLine,
                and_(
                    (
                        TradeFulfillmentLine.company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.fulfillment_id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_line_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.trade_document_id
                        == (
                            InvoiceFulfillmentAllocation
                            .order_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.trade_document_line_id
                        == (
                            InvoiceFulfillmentAllocation
                            .order_line_id
                        )
                    ),
                    (
                        TradeFulfillmentLine.product_id
                        == (
                            InvoiceFulfillmentAllocation
                            .product_id
                        )
                    ),
                ),
            )
            .join(
                DocumentLine,
                and_(
                    (
                        DocumentLine.document_id
                        == (
                            TradeFulfillmentLine
                            .warehouse_document_id
                        )
                    ),
                    (
                        DocumentLine.id
                        == (
                            TradeFulfillmentLine
                            .warehouse_document_line_id
                        )
                    ),
                    (
                        DocumentLine.product_id
                        == TradeFulfillmentLine.product_id
                    ),
                    (
                        DocumentLine.warehouse_id
                        == TradeFulfillmentLine.warehouse_id
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    (
                        Document.company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        Document.id
                        == (
                            TradeFulfillmentLine
                            .warehouse_document_id
                        )
                    ),
                ),
            )
            .where(
                (
                    InvoiceFulfillmentAllocation.company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation.status
                    == (
                        InvoiceFulfillmentAllocationStatus
                        .ACTIVE
                    )
                ),
                peer_filter,
            )
            .order_by(
                Document.document_date,
                Document.id,
                DocumentLine.id,
                InvoiceFulfillmentAllocation.id,
            )
            .with_for_update()
        )
    ).all()

    snapshots = []

    for (
        allocation,
        fulfillment_line,
        receipt_line,
        receipt_document,
    ) in rows:
        if (
            receipt_document.status
            != DocumentStatus.POSTED
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "ACTIVE purchase fulfillment allocation "
                    "must reference POSTED warehouse document"
                )
            )

        if (
            receipt_document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase receipt base requires "
                    "warehouse RECEIPT"
                )
            )

        receipt_quantity = _decimal(
            receipt_line.quantity
        )

        fulfillment_quantity = _decimal(
            fulfillment_line.quantity
        )

        if (
            receipt_quantity
            != fulfillment_quantity
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Trade fulfillment line quantity does not "
                    "match warehouse RECEIPT line quantity"
                )
            )

        snapshots.append(
            PurchaseReturnReceiptPeerSnapshot(
                source_id=allocation.id,
                receipt_document_id=receipt_document.id,
                receipt_line_id=receipt_line.id,
                event_date=receipt_document.document_date,
                receipt_quantity=receipt_quantity,
                receipt_price=_decimal(
                    receipt_line.price
                ),
                allocation_quantity=_decimal(
                    allocation.quantity
                ),
            )
        )

    expected_ids = {
        allocation.id
        for allocation
        in target_allocations
    }

    loaded_ids = {
        snapshot.source_id
        for snapshot
        in snapshots
        if snapshot.source_id
        in expected_ids
    }

    missing = (
        expected_ids
        - loaded_ids
    )

    if missing:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Target ACTIVE invoice fulfillment allocations "
                "have no valid POSTED RECEIPT source: "
                f"{sorted(missing)}"
            )
        )

    return tuple(
        snapshots
    )


def _build_receipt_base_amounts(
    *,
    peers: Iterable[
        PurchaseReturnReceiptPeerSnapshot
    ],
    requested_source_ids: Iterable[int],
    currency_code: str,
) -> dict[
    int,
    Decimal,
]:
    """
    Allocate each receipt line's exact persisted VAT-exclusive base
    across ALL ACTIVE peer allocations before selecting requested IDs.
    """

    requested = tuple(
        _positive_int(
            source_id,
            label="requested allocation id",
        )
        for source_id
        in requested_source_ids
    )

    if len(
        requested
    ) != len(
        set(
            requested
        )
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Requested allocation IDs must be unique"
            )
        )

    requested_set = set(
        requested
    )

    groups = {}

    for peer in tuple(
        peers
    ):
        if not isinstance(
            peer,
            PurchaseReturnReceiptPeerSnapshot,
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Receipt peer snapshot has invalid type"
                )
            )

        key = (
            peer.receipt_document_id,
            peer.receipt_line_id,
        )

        groups.setdefault(
            key,
            [],
        ).append(
            peer
        )

    selected = {}
    currency = _currency(
        currency_code
    )

    for key in sorted(
        groups
    ):
        group = sorted(
            groups[
                key
            ],
            key=lambda peer: (
                peer.event_date,
                peer.source_id,
            ),
        )

        first = group[0]

        for peer in group[1:]:
            if (
                peer.event_date
                != first.event_date
                or _decimal(
                    peer.receipt_quantity
                )
                != _decimal(
                    first.receipt_quantity
                )
                or _decimal(
                    peer.receipt_price
                )
                != _decimal(
                    first.receipt_price
                )
            ):
                raise (
                    PurchaseReturnRecognitionReconciliationDataIntegrityError(
                        "Receipt peers disagree on persistent "
                        "receipt-line truth"
                    )
                )

        receipt_quantity = _decimal(
            first.receipt_quantity
        )

        receipt_price = _decimal(
            first.receipt_price
        )

        if receipt_quantity <= ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Receipt quantity must be positive"
                )
            )

        if receipt_price < ZERO:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Receipt accounting price cannot be negative"
                )
            )

        receipt_base = (
            round_currency_amount(
                amount=(
                    receipt_quantity
                    * receipt_price
                ),
                currency_code=currency,
            )
        )

        candidates = tuple(
            SupplierReceiptBaseAllocationCandidate(
                source_id=peer.source_id,
                event_date=peer.event_date,
                quantity=_decimal(
                    peer.allocation_quantity
                ),
            )
            for peer
            in group
        )

        try:
            targets = (
                build_supplier_receipt_base_allocation_targets(
                    receipt_quantity=receipt_quantity,
                    receipt_base_amount=receipt_base,
                    currency_code=currency,
                    candidates=candidates,
                )
            )
        except (
            SupplierEconomicLiabilityCalculationError
        ) as exc:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Return receipt-base "
                    "allocation failed: "
                    f"{exc}"
                )
            ) from exc

        for target in targets:
            if (
                target.source_id
                not in requested_set
            ):
                continue

            if target.source_id in selected:
                raise (
                    PurchaseReturnRecognitionReconciliationDataIntegrityError(
                        "One requested allocation appears in "
                        "more than one receipt line"
                    )
                )

            selected[
                target.source_id
            ] = _decimal(
                target.amount
            )

    missing = (
        requested_set
        - set(
            selected
        )
    )

    if missing:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "ACTIVE invoice fulfillment allocations "
                "have no POSTED receipt base: "
                f"{sorted(missing)}"
            )
        )

    return selected


async def _load_purchase_capacity_sources(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    PurchaseReturnEconomicCapacity,
    ...,
]:
    discovered = (
        await _discover_target_allocations(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    if not discovered:
        return ()

    requested_ids = tuple(
        allocation.id
        for allocation
        in discovered
    )

    groups = {}

    for allocation in discovered:
        groups.setdefault(
            (
                allocation.invoice_id,
                allocation.invoice_line_id,
            ),
            [],
        ).append(
            allocation.id
        )

    locked_target_allocations = {}
    commercial_components = {}
    currencies = set()

    for (
        invoice_id,
        invoice_line_id,
    ) in sorted(
        groups
    ):
        (
            document,
            line,
            calculation,
        ) = (
            await _lock_purchase_invoice_line_context(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
            )
        )

        snapshot = (
            build_purchase_return_invoice_line_snapshot(
                document=document,
                line=line,
                calculation=calculation,
            )
        )

        (
            all_active_allocations,
            all_components,
        ) = (
            await _load_invoice_commercial_components(
                db,
                company_id=company_id,
                snapshot=snapshot,
            )
        )

        for source_id in groups[
            (
                invoice_id,
                invoice_line_id,
            )
        ]:
            allocation = (
                all_active_allocations.get(
                    source_id
                )
            )

            component = (
                all_components.get(
                    source_id
                )
            )

            if (
                allocation is None
                or component is None
            ):
                raise (
                    PurchaseReturnRecognitionReconciliationDataIntegrityError(
                        "Discovered Purchase allocation "
                        "is no longer ACTIVE"
                    )
                )

            locked_target_allocations[
                source_id
            ] = allocation

            commercial_components[
                source_id
            ] = component

            currencies.add(
                component.currency_code
            )

    if len(
        currencies
    ) != 1:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Return fulfillment line contains "
                "multiple commercial currencies"
            )
        )

    currency_code = next(
        iter(
            currencies
        )
    )

    target_allocations = tuple(
        locked_target_allocations[
            source_id
        ]
        for source_id
        in requested_ids
    )

    receipt_peers = (
        await _load_receipt_peer_snapshots(
            db,
            company_id=company_id,
            target_allocations=target_allocations,
        )
    )

    base_amounts = (
        _build_receipt_base_amounts(
            peers=receipt_peers,
            requested_source_ids=requested_ids,
            currency_code=currency_code,
        )
    )

    receipt_event_dates = {}

    requested_set = set(
        requested_ids
    )

    for peer in receipt_peers:
        if (
            peer.source_id
            not in requested_set
        ):
            continue

        if (
            peer.source_id
            in receipt_event_dates
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Target allocation appears in more than "
                    "one receipt peer row"
                )
            )

        receipt_event_dates[
            peer.source_id
        ] = peer.event_date

    capacities = []

    for source_id in requested_ids:
        allocation = (
            locked_target_allocations[
                source_id
            ]
        )

        component = (
            commercial_components[
                source_id
            ]
        )

        receipt_event_date = (
            receipt_event_dates.get(
                source_id
            )
        )

        if receipt_event_date is None:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Target allocation receipt date is missing"
                )
            )

        if (
            receipt_event_date
            != component.event_date
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase commercial allocation date "
                    "does not match receipt-base date"
                )
            )

        quantity = _decimal(
            allocation.quantity
        )

        if (
            quantity
            != component.quantity
        ):
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase commercial allocation quantity "
                    "does not match persistent allocation"
                )
            )

        capacities.append(
            PurchaseReturnEconomicCapacity(
                source_id=source_id,
                event_date=receipt_event_date,
                quantity=quantity,
                base_amount=base_amounts[
                    source_id
                ],
                gross_amount=(
                    component.gross_amount
                ),
                tax_amount=(
                    component.tax_amount
                ),
                currency_code=currency_code,
            )
        )

    return tuple(
        sorted(
            capacities,
            key=lambda capacity: (
                capacity.event_date,
                capacity.source_id,
            ),
        )
    )


async def _load_purchase_return_recognition_history(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    PurchaseReturnRecognitionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnRecognitionEvent
                )
                .join(
                    TradeReturnEvent,
                    and_(
                        (
                            TradeReturnEvent.company_id
                            == (
                                PurchaseReturnRecognitionEvent
                                .company_id
                            )
                        ),
                        (
                            TradeReturnEvent.id
                            == (
                                PurchaseReturnRecognitionEvent
                                .trade_return_event_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        PurchaseReturnRecognitionEvent
                        .company_id
                        == company_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                    (
                        TradeReturnEvent.direction
                        == "purchase"
                    ),
                )
                .order_by(
                    PurchaseReturnRecognitionEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _active_purchase_return_pair_map(
    *,
    history: Iterable[
        PurchaseReturnRecognitionEvent
    ],
) -> dict[
    tuple[int, int],
    PurchaseReturnRecognitionEvent,
]:
    active = _active_original_rows(
        history,
        label="PurchaseReturnRecognitionEvent",
    )

    result = {}

    for event in active:
        key = (
            _positive_int(
                event.trade_return_event_id,
                label="trade_return_event_id",
            ),
            _positive_int(
                event.invoice_fulfillment_allocation_id,
                label=(
                    "invoice_fulfillment_allocation_id"
                ),
            ),
        )

        if key in result:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Return recognition pair has "
                    "more than one active original"
                )
            )

        result[
            key
        ] = event

    return result


def _active_history_currency_codes(
    *,
    history: Iterable[
        PurchaseReturnRecognitionEvent
    ],
) -> set[str]:
    active = _active_original_rows(
        history,
        label="PurchaseReturnRecognitionEvent",
    )

    return {
        _currency(
            event.currency_code
        )
        for event
        in active
    }


async def reconcile_purchase_return_recognition_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    created_by: int,
    adjustment_date: date | None = None,
) -> PurchaseReturnRecognitionReconciliationResult:
    """
    Reconcile complete PURCHASE Return economic recognition for one
    original purchase fulfillment / warehouse RECEIPT line.

    Physical truth:
        active immutable PURCHASE TradeReturnEvent history.

    Economic capacities:
        ACTIVE InvoiceFulfillmentAllocation
        +
        confirmed Purchase Invoice commercial snapshot
        +
        immutable TaxCalculation snapshot
        +
        exact peer-aware historical warehouse receipt base.

    Desired immutable accounting source:
        TradeReturnEvent
        +
        InvoiceFulfillmentAllocation.

    This service intentionally does NOT:
        - post JournalEntry;
        - touch 631 / 281;
        - recognize/reverse INPUT VAT;
        - touch 641 / 644;
        - create RK;
        - reconcile supplier advances.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    fulfillment_id = _positive_int(
        fulfillment_id,
        label="fulfillment_id",
    )

    fulfillment_line_id = _positive_int(
        fulfillment_line_id,
        label="fulfillment_line_id",
    )

    created_by = _positive_int(
        created_by,
        label="created_by",
    )

    if (
        adjustment_date is not None
        and not isinstance(
            adjustment_date,
            date,
        )
    ):
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "adjustment_date must be a date"
            )
        )

    return_history = (
        await _load_trade_return_history(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    return_candidates = (
        _build_active_return_candidates(
            history=return_history
        )
    )

    capacities = (
        await _load_purchase_capacity_sources(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    history = (
        await _load_purchase_return_recognition_history(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    current_by_pair = (
        _active_purchase_return_pair_map(
            history=history
        )
    )

    currencies = {
        _currency(
            capacity.currency_code
        )
        for capacity
        in capacities
    }

    currencies.update(
        _active_history_currency_codes(
            history=history
        )
    )

    if len(
        currencies
    ) > 1:
        raise (
            PurchaseReturnRecognitionReconciliationDataIntegrityError(
                "Purchase Return fulfillment line "
                "contains multiple currencies"
            )
        )

    if not capacities:
        if return_candidates:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Active Purchase Return exists without "
                    "ACTIVE purchase economic capacity"
                )
            )

        desired_targets = ()

        currency_code = (
            next(
                iter(
                    currencies
                )
            )
            if currencies
            else None
        )

    else:
        if not currencies:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase economic capacities have no currency"
                )
            )

        currency_code = next(
            iter(
                currencies
            )
        )

        try:
            desired_targets = (
                build_purchase_return_recognition_targets(
                    capacities=capacities,
                    candidates=return_candidates,
                    currency_code=currency_code,
                )
            )
        except (
            PurchaseReturnRecognitionCalculationError
        ) as exc:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Return economic allocation failed: "
                    f"{exc}"
                )
            ) from exc

    desired_by_pair = {}

    for target in desired_targets:
        if target.pair_key in desired_by_pair:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Duplicate desired Purchase Return pair"
                )
            )

        desired_by_pair[
            target.pair_key
        ] = target

    current_keys = tuple(
        sorted(
            current_by_pair
        )
    )

    new_keys = tuple(
        sorted(
            set(
                desired_by_pair
            )
            - set(
                current_by_pair
            )
        )
    )

    created_events = []

    #
    # Existing pairs first:
    # removals / replacements happen before new increases.
    #
    for key in (
        *current_keys,
        *new_keys,
    ):
        try:
            result = (
                await reconcile_purchase_return_recognition_source(
                    db,
                    company_id=company_id,
                    trade_return_event_id=key[0],
                    invoice_fulfillment_allocation_id=key[1],
                    created_by=created_by,
                    target=desired_by_pair.get(
                        key
                    ),
                    reversal_date=adjustment_date,
                )
            )
        except (
            PurchaseReturnRecognitionPersistenceError
        ) as exc:
            raise (
                PurchaseReturnRecognitionReconciliationDataIntegrityError(
                    "Purchase Return immutable persistence "
                    f"failed for pair {key}: {exc}"
                )
            ) from exc

        created_events.extend(
            result.created_events
        )

    return PurchaseReturnRecognitionReconciliationResult(
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=(
            fulfillment_line_id
        ),
        currency_code=currency_code,
        return_candidates=return_candidates,
        capacities=capacities,
        desired_targets=desired_targets,
        current_pair_keys=current_keys,
        created_events=tuple(
            created_events
        ),
    )
