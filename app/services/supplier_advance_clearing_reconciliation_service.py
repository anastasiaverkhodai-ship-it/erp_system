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

from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.document_line import DocumentLine
from app.models.input_vat_fulfillment_bridge_event import (
    InputVatFulfillmentBridgeEvent,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.supplier_advance_clearing_event import (
    SupplierAdvanceClearingEvent,
)
from app.models.trade_document import TradeDocument
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)

from app.services.supplier_economic_liability_calculation_service import (
    SupplierEconomicLiabilityBaseAdjustment,
    build_supplier_economic_liability_candidates_with_base_adjustments,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeDataIntegrityError,
)
from app.services.input_vat_fulfillment_bridge_persistence_service import (
    build_current_input_vat_fulfillment_bridge_targets,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)
from app.services.supplier_advance_clearing_calculation_service import (
    SupplierAdvanceClearingCalculationError,
    SupplierAdvanceClearingTarget,
    SupplierAdvanceSettlementCandidate,
    SupplierEconomicLiabilityCandidate,
    build_supplier_advance_clearing_targets,
)
from app.services.supplier_advance_clearing_persistence_service import (
    SupplierAdvanceClearingDataIntegrityError,
    build_current_supplier_advance_clearing_targets,
    reconcile_supplier_advance_clearing_source,
)
from app.services.supplier_economic_liability_calculation_service import (
    SupplierEconomicLiabilityCalculationError,
    SupplierReceiptBaseAllocationCandidate,
    SupplierReceiptBaseAllocationTarget,
    SupplierVatLiabilityComponent,
    build_supplier_economic_liability_candidates,
    build_supplier_receipt_base_allocation_targets,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)

from app.models.purchase_value_correction_vat_adjustment_event import (
    PurchaseValueCorrectionVatAdjustmentEvent,
)
from app.services.purchase_value_correction_supplier_vat_liability_calculation_service import (
    PurchaseValueCorrectionSupplierVatLiabilityCalculationError,
    SupplierVatAllocationCandidate,
    build_purchase_value_correction_supplier_vat_adjustments,
)


ZERO = Decimal("0")


class SupplierAdvanceClearingReconciliationError(
    Exception
):
    """Base supplier-advance reconciliation error."""


class SupplierAdvanceClearingInvoiceNotFoundError(
    SupplierAdvanceClearingReconciliationError
):
    """Purchase Invoice does not exist."""


class SupplierAdvanceClearingOpenItemNotFoundError(
    SupplierAdvanceClearingReconciliationError
):
    """Required PAYABLE Open Item does not exist."""


class SupplierAdvanceClearingReconciliationDataIntegrityError(
    SupplierAdvanceClearingReconciliationError
):
    """Persistent reconciliation inputs are inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierReceiptPeerSnapshot:
    """
    One ACTIVE InvoiceFulfillmentAllocation consuming
    one POSTED warehouse RECEIPT line.

    receipt_price is already the VAT-exclusive warehouse
    accounting price persisted by the Purchase Receipt
    normalization layer.
    """

    source_id: int
    invoice_id: int
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
class SupplierAdvanceClearingReconciliationResult:
    invoice_id: int

    settlement_candidates: tuple[
        SupplierAdvanceSettlementCandidate,
        ...,
    ]

    liability_candidates: tuple[
        SupplierEconomicLiabilityCandidate,
        ...,
    ]

    current_targets: tuple[
        SupplierAdvanceClearingTarget,
        ...,
    ]

    desired_targets: tuple[
        SupplierAdvanceClearingTarget,
        ...,
    ]

    reconciliation_targets: tuple[
        SupplierAdvanceClearingTarget,
        ...,
    ]

    created_events: tuple[
        SupplierAdvanceClearingEvent,
        ...,
    ]

    @property
    def created_event_ids(
        self,
    ) -> tuple[int, ...]:
        ids = []

        for event in self.created_events:
            if (
                event.id is None
                or event.id <= 0
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "Created supplier advance clearing "
                        "event must have a persistent "
                        "positive ID"
                    )
                )

            ids.append(
                int(event.id)
            )

        return tuple(ids)


def _decimal(
    value,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Monetary or quantity value "
                "is not a valid Decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Monetary or quantity value "
                "must be finite"
            )
        )

    return result


def _currency(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "currency_code must be a string"
            )
        )

    currency = (
        value
        .strip()
        .upper()
    )

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "currency_code must contain "
                "exactly three letters"
            )
        )

    return currency


def _positive_id(
    value,
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
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                f"{label} must be a positive integer"
            )
        )

    return value


def _pair_key(
    target: SupplierAdvanceClearingTarget,
) -> tuple[int, int]:
    return (
        target.settlement_source_id,
        target.liability_source_id,
    )


def _target_map(
    targets: Iterable[
        SupplierAdvanceClearingTarget
    ],
    *,
    label: str,
) -> dict[
    tuple[int, int],
    SupplierAdvanceClearingTarget,
]:
    result = {}

    for target in tuple(
        targets
    ):
        if not isinstance(
            target,
            SupplierAdvanceClearingTarget,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    f"{label} target must be "
                    "SupplierAdvanceClearingTarget"
                )
            )

        key = _pair_key(
            target
        )

        if key in result:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    f"Duplicate {label} supplier "
                    "advance clearing source pair"
                )
            )

        result[
            key
        ] = target

    return result


def _validate_same_pair_provenance(
    *,
    current: SupplierAdvanceClearingTarget,
    desired: SupplierAdvanceClearingTarget,
) -> None:
    if (
        current.event_date
        < desired.event_date
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier advance clearing current "
                "accounting date precedes desired "
                "source-eligible event_date"
            )
        )

    if (
        current.currency_code
        != desired.currency_code
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier advance clearing "
                "currency changed for an "
                "existing source pair"
            )
        )


def build_supplier_advance_clearing_reconciliation_targets(
    *,
    desired_targets: Iterable[
        SupplierAdvanceClearingTarget
    ],
    current_targets: Iterable[
        SupplierAdvanceClearingTarget
    ],
) -> tuple[
    SupplierAdvanceClearingTarget,
    ...,
]:
    """
    Convert complete desired clearing state into ordered
    persistence actions.

    Rules:
      1. Current pair absent from desired state -> zero.
      2. Amount decreases/removals run before increases.
      3. Exact matches are omitted.
      4. New zero targets are omitted.
      5. Existing source-pair currency cannot change.
         Persisted accounting date may be later than the
         desired source-eligible event date, never earlier.
    """

    desired_by_pair = (
        _target_map(
            desired_targets,
            label="desired",
        )
    )

    current_by_pair = (
        _target_map(
            current_targets,
            label="current",
        )
    )

    decreases = []
    increases = []

    for (
        key,
        current,
    ) in current_by_pair.items():
        desired = (
            desired_by_pair.get(
                key
            )
        )

        if desired is None:
            decreases.append(
                SupplierAdvanceClearingTarget(
                    settlement_source_id=(
                        current
                        .settlement_source_id
                    ),
                    liability_source_id=(
                        current
                        .liability_source_id
                    ),
                    event_date=(
                        current.event_date
                    ),
                    amount=ZERO,
                    currency_code=(
                        current.currency_code
                    ),
                )
            )
            continue

        _validate_same_pair_provenance(
            current=current,
            desired=desired,
        )

        current_amount = _decimal(
            current.amount
        )

        desired_amount = _decimal(
            desired.amount
        )

        if (
            current_amount
            == desired_amount
        ):
            continue

        if (
            desired_amount
            < current_amount
        ):
            decreases.append(
                desired
            )
        else:
            increases.append(
                desired
            )

    for (
        key,
        desired,
    ) in desired_by_pair.items():
        if key in current_by_pair:
            continue

        if (
            _decimal(
                desired.amount
            )
            == ZERO
        ):
            continue

        increases.append(
            desired
        )

    def sort_key(
        target: SupplierAdvanceClearingTarget,
    ):
        return (
            target.event_date,
            target.settlement_source_id,
            target.liability_source_id,
        )

    return tuple(
        [
            *sorted(
                decreases,
                key=sort_key,
            ),
            *sorted(
                increases,
                key=sort_key,
            ),
        ]
    )


def build_supplier_receipt_base_targets_for_invoice(
    *,
    peers: Iterable[
        SupplierReceiptPeerSnapshot
    ],
    invoice_source_ids: Iterable[int],
    currency_code: str,
) -> tuple[
    SupplierReceiptBaseAllocationTarget,
    ...,
]:
    """
    Allocate each POSTED receipt line's persisted accounting
    base across ALL ACTIVE peer InvoiceFulfillmentAllocations.

    The current invoice's sources are selected only after
    complete peer-aware cumulative rounding has been done.
    """

    currency = _currency(
        currency_code
    )

    requested_ids = tuple(
        _positive_id(
            source_id,
            label="Invoice fulfillment source ID",
        )
        for source_id
        in invoice_source_ids
    )

    if (
        len(requested_ids)
        != len(
            set(
                requested_ids
            )
        )
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Invoice fulfillment source IDs "
                "must be unique"
            )
        )

    requested_set = set(
        requested_ids
    )

    groups = {}

    for peer in tuple(
        peers
    ):
        if not isinstance(
            peer,
            SupplierReceiptPeerSnapshot,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt peer must be "
                    "SupplierReceiptPeerSnapshot"
                )
            )

        _positive_id(
            peer.source_id,
            label="Peer source ID",
        )

        _positive_id(
            peer.invoice_id,
            label="Peer invoice ID",
        )

        _positive_id(
            peer.receipt_document_id,
            label="Receipt document ID",
        )

        _positive_id(
            peer.receipt_line_id,
            label="Receipt line ID",
        )

        if not isinstance(
            peer.event_date,
            date,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt peer event_date "
                    "must be a date"
                )
            )

        receipt_quantity = _decimal(
            peer.receipt_quantity
        )

        receipt_price = _decimal(
            peer.receipt_price
        )

        allocation_quantity = _decimal(
            peer.allocation_quantity
        )

        if receipt_quantity <= ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt quantity must be positive"
                )
            )

        if receipt_price < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt accounting price "
                    "cannot be negative"
                )
            )

        if allocation_quantity <= ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Allocation quantity must be positive"
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
            SupplierReceiptPeerSnapshot(
                source_id=peer.source_id,
                invoice_id=peer.invoice_id,
                receipt_document_id=(
                    peer.receipt_document_id
                ),
                receipt_line_id=(
                    peer.receipt_line_id
                ),
                event_date=peer.event_date,
                receipt_quantity=(
                    receipt_quantity
                ),
                receipt_price=(
                    receipt_price
                ),
                allocation_quantity=(
                    allocation_quantity
                ),
            )
        )

    selected_targets = []

    seen_requested_ids = set()

    for key in sorted(
        groups
    ):
        group = groups[
            key
        ]

        first = group[0]

        for peer in group[1:]:
            if (
                peer.event_date
                != first.event_date
                or peer.receipt_quantity
                != first.receipt_quantity
                or peer.receipt_price
                != first.receipt_price
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "Receipt peers disagree on "
                        "persistent receipt-line truth"
                    )
                )

        receipt_base_amount = (
            round_currency_amount(
                amount=(
                    first.receipt_quantity
                    * first.receipt_price
                ),
                currency_code=currency,
            )
        )

        candidates = tuple(
            SupplierReceiptBaseAllocationCandidate(
                source_id=(
                    peer.source_id
                ),
                event_date=(
                    peer.event_date
                ),
                quantity=(
                    peer.allocation_quantity
                ),
            )
            for peer in group
        )

        try:
            targets = (
                build_supplier_receipt_base_allocation_targets(
                    receipt_quantity=(
                        first.receipt_quantity
                    ),
                    receipt_base_amount=(
                        receipt_base_amount
                    ),
                    currency_code=currency,
                    candidates=candidates,
                )
            )
        except (
            SupplierEconomicLiabilityCalculationError
        ) as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier receipt-base "
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

            if (
                target.source_id
                in seen_requested_ids
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "Invoice fulfillment source "
                        "appears in more than one "
                        "receipt line"
                    )
                )

            seen_requested_ids.add(
                target.source_id
            )

            selected_targets.append(
                target
            )

    missing = (
        requested_set
        - seen_requested_ids
    )

    if missing:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "ACTIVE invoice fulfillment "
                "sources have no POSTED receipt "
                f"base: {sorted(missing)}"
            )
        )

    return tuple(
        sorted(
            selected_targets,
            key=lambda target: (
                target.event_date,
                target.source_id,
            ),
        )
    )


def _validate_purchase_invoice(
    invoice: TradeDocument,
    *,
    company_id: int,
    invoice_id: int,
) -> str:
    if (
        invoice.company_id
        != company_id
        or invoice.id
        != invoice_id
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Invoice identity mismatch"
            )
        )

    if (
        invoice.direction
        != TradeDirection.PURCHASE
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier advance clearing "
                "requires PURCHASE invoice"
            )
        )

    if (
        invoice.kind
        != TradeDocumentKind.INVOICE
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier advance clearing "
                "requires Trade Invoice"
            )
        )

    if (
        invoice.status
        == TradeDocumentStatus.DRAFT
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Draft Purchase Invoice cannot "
                "participate in supplier clearing"
            )
        )

    return _currency(
        invoice.currency_code
    )


def _validate_open_item(
    item: CounterpartyOpenItem,
    *,
    invoice: TradeDocument,
    currency_code: str,
) -> None:
    if (
        item.company_id
        != invoice.company_id
        or item.trade_document_id
        != invoice.id
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Invoice open item "
                "source mismatch"
            )
        )

    if (
        item.item_type
        != CounterpartyOpenItemType.PAYABLE
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier clearing requires "
                "PAYABLE open item"
            )
        )

    if (
        _currency(
            item.currency_code
        )
        != currency_code
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Invoice and PAYABLE "
                "open item currency mismatch"
            )
        )

    if (
        _decimal(
            item.original_amount
        )
        <= ZERO
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "PAYABLE open item original "
                "amount must be positive"
            )
        )


async def _lock_purchase_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> TradeDocument:
    invoice = (
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
            .with_for_update()
        )
    ).scalar_one_or_none()

    if invoice is None:
        raise (
            SupplierAdvanceClearingInvoiceNotFoundError(
                "Purchase Invoice not found"
            )
        )

    return invoice


async def _load_purchase_open_item(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> CounterpartyOpenItem:
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            )
            .where(
                CounterpartyOpenItem.company_id
                == company_id,
                CounterpartyOpenItem.trade_document_id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if item is None:
        raise (
            SupplierAdvanceClearingOpenItemNotFoundError(
                "Purchase Invoice PAYABLE "
                "open item not found"
            )
        )

    return item


async def _load_supplier_settlement_candidates(
    db: AsyncSession,
    *,
    invoice: TradeDocument,
    open_item: CounterpartyOpenItem,
    currency_code: str,
) -> tuple[
    SupplierAdvanceSettlementCandidate,
    ...,
]:
    rows = (
        await db.execute(
            select(
                PaymentSettlementAllocation,
                Payment,
            )
            .join(
                Payment,
                and_(
                    Payment.company_id
                    == (
                        PaymentSettlementAllocation
                        .company_id
                    ),
                    Payment.id
                    == (
                        PaymentSettlementAllocation
                        .payment_id
                    ),
                ),
            )
            .where(
                (
                    PaymentSettlementAllocation
                    .company_id
                    == invoice.company_id
                ),
                (
                    PaymentSettlementAllocation
                    .open_item_id
                    == open_item.id
                ),
                (
                    PaymentSettlementAllocation
                    .status
                    == (
                        PaymentSettlementAllocationStatus
                        .ACTIVE
                    )
                ),
            )
            .order_by(
                Payment.payment_date,
                PaymentSettlementAllocation.id,
            )
        )
    ).all()

    candidates = []

    for (
        allocation,
        payment,
    ) in rows:
        if (
            payment.status
            != PaymentStatus.CONFIRMED
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "ACTIVE supplier settlement "
                    "must reference CONFIRMED payment"
                )
            )

        if (
            payment.direction
            != PaymentDirection.OUTGOING
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier settlement must "
                    "reference OUTGOING payment"
                )
            )

        if (
            _currency(
                payment.currency_code
            )
            != currency_code
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier payment currency "
                    "does not match Purchase Invoice"
                )
            )

        amount = _decimal(
            allocation.amount
        )

        if amount <= ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "ACTIVE supplier settlement "
                    "amount must be positive"
                )
            )

        candidates.append(
            SupplierAdvanceSettlementCandidate(
                source_id=allocation.id,
                event_date=(
                    payment.payment_date
                ),
                amount=amount,
            )
        )

    return tuple(
        candidates
    )


async def _load_invoice_fulfillment_allocations(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> tuple[
    InvoiceFulfillmentAllocation,
    ...,
]:
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
                        .invoice_id
                        == invoice_id
                    ),
                )
                .order_by(
                    InvoiceFulfillmentAllocation.id
                )
            )
        )
        .scalars()
        .all()
    )


async def _load_receipt_peer_snapshots(
    db: AsyncSession,
    *,
    company_id: int,
    active_invoice_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
) -> tuple[
    SupplierReceiptPeerSnapshot,
    ...,
]:
    if not active_invoice_allocations:
        return ()

    peer_keys = tuple(
        sorted(
            {
                (
                    allocation.fulfillment_id,
                    allocation.fulfillment_line_id,
                )
                for allocation
                in active_invoice_allocations
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
                        TradeFulfillmentLine
                        .company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        TradeFulfillmentLine
                        .fulfillment_id
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
                        TradeFulfillmentLine
                        .trade_document_id
                        == (
                            InvoiceFulfillmentAllocation
                            .order_id
                        )
                    ),
                    (
                        TradeFulfillmentLine
                        .trade_document_line_id
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
                    Document.company_id
                    == (
                        InvoiceFulfillmentAllocation
                        .company_id
                    ),
                    Document.id
                    == (
                        TradeFulfillmentLine
                        .warehouse_document_id
                    ),
                ),
            )
            .where(
                (
                    InvoiceFulfillmentAllocation
                    .company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .status
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
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "ACTIVE purchase fulfillment "
                    "allocation must reference "
                    "POSTED warehouse document"
                )
            )

        if (
            receipt_document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier economic liability "
                    "requires warehouse RECEIPT"
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
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Trade fulfillment line quantity "
                    "does not match warehouse "
                    "RECEIPT line quantity"
                )
            )

        snapshots.append(
            SupplierReceiptPeerSnapshot(
                source_id=allocation.id,
                invoice_id=allocation.invoice_id,
                receipt_document_id=(
                    receipt_document.id
                ),
                receipt_line_id=(
                    receipt_line.id
                ),
                event_date=(
                    receipt_document.document_date
                ),
                receipt_quantity=(
                    receipt_quantity
                ),
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
        in active_invoice_allocations
    }

    loaded_ids = {
        snapshot.source_id
        for snapshot in snapshots
        if snapshot.source_id
        in expected_ids
    }

    missing = (
        expected_ids
        - loaded_ids
    )

    if missing:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "ACTIVE invoice fulfillment "
                "allocations have no valid "
                "POSTED RECEIPT source: "
                f"{sorted(missing)}"
            )
        )

    return tuple(
        snapshots
    )


async def _load_supplier_vat_components(
    db: AsyncSession,
    *,
    company_id: int,
    all_invoice_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
    active_invoice_source_ids: set[int],
    currency_code: str,
) -> tuple[
    SupplierVatLiabilityComponent,
    ...,
]:
    all_source_ids = tuple(
        allocation.id
        for allocation
        in all_invoice_allocations
    )

    if not all_source_ids:
        return ()

    events = tuple(
        (
            await db.execute(
                select(
                    InputVatFulfillmentBridgeEvent
                )
                .where(
                    (
                        InputVatFulfillmentBridgeEvent
                        .company_id
                        == company_id
                    ),
                    (
                        InputVatFulfillmentBridgeEvent
                        .invoice_fulfillment_allocation_id
                        .in_(
                            all_source_ids
                        )
                    ),
                )
                .order_by(
                    (
                        InputVatFulfillmentBridgeEvent
                        .tax_calculation_id
                    ),
                    InputVatFulfillmentBridgeEvent.id,
                )
            )
        )
        .scalars()
        .all()
    )

    if not events:
        return ()

    events_by_calculation = {}

    for event in events:
        events_by_calculation.setdefault(
            event.tax_calculation_id,
            [],
        ).append(
            event
        )

    components = []

    for calculation_id in sorted(
        events_by_calculation
    ):
        try:
            current_targets = (
                build_current_input_vat_fulfillment_bridge_targets(
                    events=tuple(
                        events_by_calculation[
                            calculation_id
                        ]
                    ),
                    currency_code=currency_code,
                )
            )
        except (
            InputVatFulfillmentBridgeDataIntegrityError
        ) as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Could not rebuild current "
                    "INPUT VAT fulfillment bridge "
                    f"state: {exc}"
                )
            ) from exc

        for target in current_targets:
            if (
                target.source_id
                not in active_invoice_source_ids
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "Active INPUT VAT economic "
                        "bridge exists for a non-active "
                        "InvoiceFulfillmentAllocation"
                    )
                )

            components.append(
                SupplierVatLiabilityComponent(
                    source_id=target.source_id,
                    event_date=target.event_date,
                    amount=target.amount,
                )
            )

    return tuple(
        sorted(
            components,
            key=lambda component: (
                component.event_date,
                component.source_id,
            ),
        )
    )


def apply_purchase_return_base_to_supplier_receipt_targets(
    *,
    base_targets: tuple[
        SupplierReceiptBaseAllocationTarget,
        ...,
    ],
    active_return_base_by_source: dict[
        int,
        Decimal,
    ],
    currency_code: str,
) -> tuple[
    SupplierReceiptBaseAllocationTarget,
    ...,
]:
    """
    Reduce receipt-base supplier liability by ACTIVE immutable
    PurchaseReturnRecognitionEvent.returned_base_amount.

    Economic 631 truth at the Purchase Return milestone:

        current base liability
            = original posted receipt base
            - ACTIVE Purchase Return recognized base.

    INPUT VAT is intentionally NOT reduced inside this base helper.
    The supplier economic-liability loader separately reduces current
    INPUT VAT components by ACTIVE PurchaseReturnVatAdjustmentEvent
    adjusted_tax_amount.

    The economic-liability event date remains the original receipt date;
    Purchase Return changes capacity, not the historical source identity.
    """
    currency = _currency(
        currency_code
    )

    normalized_returns: dict[
        int,
        Decimal,
    ] = {}

    for (
        raw_source_id,
        raw_amount,
    ) in active_return_base_by_source.items():
        source_id = _positive_id(
            raw_source_id,
            label="Purchase Return liability source ID",
        )

        try:
            amount = round_currency_amount(
                amount=_decimal(
                    raw_amount
                ),
                currency_code=currency,
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return returned base "
                    "cannot be rounded"
                )
            ) from exc

        if amount < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Return returned base "
                    "cannot be negative"
                )
            )

        normalized_returns[
            source_id
        ] = amount

    base_source_ids = {
        _positive_id(
            target.source_id,
            label="Receipt-base source ID",
        )
        for target in base_targets
    }

    unknown_return_sources = (
        set(
            normalized_returns
        )
        - base_source_ids
    )

    if unknown_return_sources:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Active Purchase Return base references "
                "a source with no current receipt base: "
                f"{sorted(unknown_return_sources)}"
            )
        )

    adjusted = []

    for target in base_targets:
        if not isinstance(
            target,
            SupplierReceiptBaseAllocationTarget,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt base target must be "
                    "SupplierReceiptBaseAllocationTarget"
                )
            )

        source_id = _positive_id(
            target.source_id,
            label="Receipt-base source ID",
        )

        if (
            _currency(
                target.currency_code
            )
            != currency
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt base target currency differs "
                    "from reconciliation currency"
                )
            )

        try:
            original_base = round_currency_amount(
                amount=_decimal(
                    target.amount
                ),
                currency_code=currency,
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt base amount cannot be rounded"
                )
            ) from exc

        if original_base < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Receipt base amount cannot be negative"
                )
            )

        returned_base = (
            normalized_returns.get(
                source_id,
                ZERO,
            )
        )

        try:
            current_base = round_currency_amount(
                amount=(
                    original_base
                    - returned_base
                ),
                currency_code=currency,
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Current supplier receipt base "
                    "cannot be rounded"
                )
            ) from exc

        if current_base < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Return base exceeds "
                    "the allocated posted receipt base"
                )
            )

        adjusted.append(
            SupplierReceiptBaseAllocationTarget(
                source_id=source_id,
                event_date=target.event_date,
                amount=current_base,
                currency_code=currency,
            )
        )

    return tuple(
        adjusted
    )


async def _load_active_purchase_return_base_by_source(
    db: AsyncSession,
    *,
    company_id: int,
    active_source_ids: set[int],
    currency_code: str,
) -> dict[
    int,
    Decimal,
]:
    """
    Rebuild ACTIVE Purchase Return Recognition base by
    InvoiceFulfillmentAllocation.

    Immutable history:

        original PRRE
            contributes returned_base_amount

        immutable PRRE reversal
            removes its original from current state

        immutable replacement
            contributes its own full returned_base_amount

    All history rows for the affected liability sources are locked.
    """
    if not active_source_ids:
        return {}

    source_ids = {
        _positive_id(
            source_id,
            label="Active liability source ID",
        )
        for source_id
        in active_source_ids
    }

    currency = _currency(
        currency_code
    )

    events = tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnRecognitionEvent
                )
                .where(
                    (
                        PurchaseReturnRecognitionEvent
                        .company_id
                        == company_id
                    ),
                    (
                        PurchaseReturnRecognitionEvent
                        .invoice_fulfillment_allocation_id
                        .in_(
                            tuple(
                                sorted(
                                    source_ids
                                )
                            )
                        )
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

    if not events:
        return {}

    by_id = {}
    originals = {}
    reversals = {}

    for event in events:
        event_id = _positive_id(
            event.id,
            label="Purchase Return recognition event ID",
        )

        if event_id in by_id:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Duplicate Purchase Return recognition "
                    "history event ID"
                )
            )

        source_id = _positive_id(
            event.invoice_fulfillment_allocation_id,
            label=(
                "Purchase Return "
                "invoice_fulfillment_allocation_id"
            ),
        )

        if source_id not in source_ids:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition source "
                    "is outside current invoice liability sources"
                )
            )

        if (
            _currency(
                event.currency_code
            )
            != currency
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition currency "
                    "differs from supplier liability currency"
                )
            )

        by_id[
            event_id
        ] = event

        if event.reversal_of_id is None:
            originals[
                event_id
            ] = event
            continue

        reversal_of_id = _positive_id(
            event.reversal_of_id,
            label=(
                "Purchase Return recognition reversal_of_id"
            ),
        )

        if reversal_of_id in reversals:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition original "
                    "has more than one reversal"
                )
            )

        reversals[
            reversal_of_id
        ] = event

    unknown_reversal_sources = (
        set(
            reversals
        )
        - set(
            originals
        )
    )

    if unknown_reversal_sources:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Return recognition reversal "
                "references a non-original event"
            )
        )

    for (
        original_id,
        reversal,
    ) in reversals.items():
        original = originals[
            original_id
        ]

        if (
            reversal
            .invoice_fulfillment_allocation_id
            != original
            .invoice_fulfillment_allocation_id
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition reversal "
                    "changed liability source provenance"
                )
            )

    totals: dict[
        int,
        Decimal,
    ] = {}

    for (
        event_id,
        event,
    ) in originals.items():
        if event_id in reversals:
            continue

        source_id = int(
            event.invoice_fulfillment_allocation_id
        )

        try:
            returned_base = round_currency_amount(
                amount=_decimal(
                    event.returned_base_amount
                ),
                currency_code=currency,
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition base "
                    "cannot be rounded"
                )
            ) from exc

        if returned_base < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Return recognition "
                    "base cannot be negative"
                )
            )

        totals[
            source_id
        ] = (
            totals.get(
                source_id,
                ZERO,
            )
            + returned_base
        )

    return totals


def apply_purchase_return_vat_to_supplier_vat_components(
    *,
    vat_components: tuple[
        SupplierVatLiabilityComponent,
        ...,
    ],
    active_return_vat_by_source: dict[
        int,
        Decimal,
    ],
    currency_code: str,
) -> tuple[
    SupplierVatLiabilityComponent,
    ...,
]:
    """
    Reduce current supplier INPUT VAT liability components by
    ACTIVE immutable PurchaseReturnVatAdjustmentEvent tax amounts.

    Economic 631 truth:

        current VAT liability
            = ACTIVE INPUT VAT fulfillment bridge
            - ACTIVE Purchase Return VAT adjustment tax amount.

    adjusted_taxable_base is intentionally irrelevant here.

    Liability source identity and event_date remain the original
    InvoiceFulfillmentAllocation / receipt economic source.
    """

    currency = _currency(
        currency_code
    )

    grouped = {}

    for component in vat_components:
        if not isinstance(
            component,
            SupplierVatLiabilityComponent,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT component must be "
                    "SupplierVatLiabilityComponent"
                )
            )

        source_id = _positive_id(
            component.source_id,
            label="Supplier VAT source ID",
        )

        if not isinstance(
            component.event_date,
            date,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT component event_date "
                    "must be a date"
                )
            )

        try:
            amount = round_currency_amount(
                amount=_decimal(
                    component.amount
                ),
                currency_code=currency,
            )

        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT component amount "
                    "cannot be rounded"
                )
            ) from exc

        if amount <= ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT component amount "
                    "must be greater than zero"
                )
            )

        existing = grouped.get(
            source_id
        )

        if existing is None:
            grouped[
                source_id
            ] = [
                component.event_date,
                amount,
            ]
            continue

        if (
            existing[
                0
            ]
            != component.event_date
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT components for one "
                    "liability source disagree on event_date"
                )
            )

        existing[
            1
        ] = round_currency_amount(
            amount=(
                existing[
                    1
                ]
                + amount
            ),
            currency_code=currency,
        )

    reductions = {}

    for (
        raw_source_id,
        raw_amount,
    ) in active_return_vat_by_source.items():
        source_id = _positive_id(
            raw_source_id,
            label=(
                "Purchase Return VAT "
                "liability source ID"
            ),
        )

        try:
            amount = round_currency_amount(
                amount=_decimal(
                    raw_amount
                ),
                currency_code=currency,
            )

        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT reduction "
                    "cannot be rounded"
                )
            ) from exc

        if amount < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Return VAT reduction "
                    "cannot be negative"
                )
            )

        if amount == ZERO:
            continue

        reductions[
            source_id
        ] = amount

    unknown_sources = (
        set(
            reductions
        )
        - set(
            grouped
        )
    )

    if unknown_sources:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Active Purchase Return VAT reduction "
                "has no current INPUT VAT liability "
                f"component: {sorted(unknown_sources)}"
            )
        )

    adjusted = []

    for source_id in sorted(
        grouped,
        key=lambda value: (
            grouped[
                value
            ][0],
            value,
        ),
    ):
        (
            event_date,
            current_vat,
        ) = grouped[
            source_id
        ]

        reduction = reductions.get(
            source_id,
            ZERO,
        )

        try:
            net_vat = round_currency_amount(
                amount=(
                    current_vat
                    - reduction
                ),
                currency_code=currency,
            )

        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Net supplier VAT liability "
                    "cannot be rounded"
                )
            ) from exc

        if net_vat < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Return VAT adjustment "
                    "exceeds current INPUT VAT "
                    f"liability for source {source_id}"
                )
            )

        if net_vat == ZERO:
            continue

        adjusted.append(
            SupplierVatLiabilityComponent(
                source_id=source_id,
                event_date=event_date,
                amount=net_vat,
            )
        )

    return tuple(
        adjusted
    )


async def _load_active_purchase_return_vat_by_source(
    db: AsyncSession,
    *,
    company_id: int,
    active_source_ids: set[int],
    currency_code: str,
) -> dict[
    int,
    Decimal,
]:
    """
    Rebuild ACTIVE economic Purchase Return VAT reduction by
    InvoiceFulfillmentAllocation.

    Immutable PurchaseReturnVatAdjustmentEvent history is resolved
    first. Only active originals contribute adjusted_tax_amount.

    PurchaseReturnRecognitionEvent is used only as provenance from
    immutable VAT source to InvoiceFulfillmentAllocation.

    Legal INPUT VAT credit correction is deliberately absent:
    Dr644/Cr641 does not change account 631 supplier liability.
    """

    if not active_source_ids:
        return {}

    source_ids = {
        _positive_id(
            source_id,
            label="Active supplier liability source ID",
        )
        for source_id
        in active_source_ids
    }

    currency = _currency(
        currency_code
    )

    prre_rows = (
        await db.execute(
            select(
                PurchaseReturnRecognitionEvent.id,
                (
                    PurchaseReturnRecognitionEvent
                    .invoice_fulfillment_allocation_id
                ),
            )
            .where(
                (
                    PurchaseReturnRecognitionEvent.company_id
                    == company_id
                ),
                (
                    PurchaseReturnRecognitionEvent
                    .invoice_fulfillment_allocation_id
                    .in_(
                        tuple(
                            sorted(
                                source_ids
                            )
                        )
                    )
                ),
            )
            .order_by(
                PurchaseReturnRecognitionEvent.id
            )
            .with_for_update()
        )
    ).all()

    if not prre_rows:
        return {}

    source_by_prre_id = {}

    for row in prre_rows:
        prre_id = _positive_id(
            row[
                0
            ],
            label="Purchase Return recognition event ID",
        )

        source_id = _positive_id(
            row[
                1
            ],
            label=(
                "Purchase Return recognition "
                "InvoiceFulfillmentAllocation ID"
            ),
        )

        if source_id not in source_ids:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return recognition VAT provenance "
                    "is outside active supplier liability sources"
                )
            )

        if prre_id in source_by_prre_id:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Duplicate Purchase Return recognition "
                    "event ID while rebuilding VAT liability"
                )
            )

        source_by_prre_id[
            prre_id
        ] = source_id

    prre_ids = tuple(
        sorted(
            source_by_prre_id
        )
    )

    events = tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnVatAdjustmentEvent
                )
                .where(
                    (
                        PurchaseReturnVatAdjustmentEvent.company_id
                        == company_id
                    ),
                    (
                        PurchaseReturnVatAdjustmentEvent
                        .purchase_return_recognition_event_id
                        .in_(
                            prre_ids
                        )
                    ),
                )
                .order_by(
                    PurchaseReturnVatAdjustmentEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    if not events:
        return {}

    originals = {}
    reversals = {}

    for event in events:
        event_id = _positive_id(
            event.id,
            label=(
                "Purchase Return VAT adjustment event ID"
            ),
        )

        prre_id = _positive_id(
            (
                event
                .purchase_return_recognition_event_id
            ),
            label=(
                "Purchase Return VAT adjustment "
                "recognition source ID"
            ),
        )

        if prre_id not in source_by_prre_id:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT adjustment references "
                    "recognition source outside current "
                    "supplier liability provenance"
                )
            )

        _positive_id(
            event.tax_calculation_id,
            label=(
                "Purchase Return VAT adjustment "
                "TaxCalculation ID"
            ),
        )

        basis_kind = str(
            event.basis_kind
        ).strip()

        if not basis_kind:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT adjustment "
                    "basis_kind cannot be blank"
                )
            )

        if (
            _currency(
                event.currency_code
            )
            != currency
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT adjustment currency "
                    "differs from supplier liability currency"
                )
            )

        try:
            adjusted_tax = round_currency_amount(
                amount=_decimal(
                    event.adjusted_tax_amount
                ),
                currency_code=currency,
            )

        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT adjusted tax "
                    "cannot be rounded"
                )
            ) from exc

        if adjusted_tax < ZERO:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT adjusted tax "
                    "cannot be negative"
                )
            )

        if event.reversal_of_id is None:
            if event_id in originals:
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "Duplicate Purchase Return VAT "
                        "original event ID"
                    )
                )

            originals[
                event_id
            ] = event
            continue

        reversal_of_id = _positive_id(
            event.reversal_of_id,
            label=(
                "Purchase Return VAT adjustment "
                "reversal_of_id"
            ),
        )

        if reversal_of_id in reversals:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT original "
                    "has more than one reversal"
                )
            )

        reversals[
            reversal_of_id
        ] = event

    unknown_reversal_sources = (
        set(
            reversals
        )
        - set(
            originals
        )
    )

    if unknown_reversal_sources:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Return VAT reversal references "
                "a non-original event"
            )
        )

    for (
        original_id,
        reversal,
    ) in reversals.items():
        original = originals[
            original_id
        ]

        if (
            reversal.purchase_return_recognition_event_id
            != original.purchase_return_recognition_event_id
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT reversal changed "
                    "recognition-event provenance"
                )
            )

        if (
            reversal.tax_calculation_id
            != original.tax_calculation_id
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT reversal changed "
                    "TaxCalculation provenance"
                )
            )

        if (
            str(
                reversal.basis_kind
            )
            != str(
                original.basis_kind
            )
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Return VAT reversal changed "
                    "basis_kind provenance"
                )
            )

    totals = {}

    for (
        event_id,
        event,
    ) in originals.items():
        if event_id in reversals:
            continue

        source_id = source_by_prre_id[
            int(
                event
                .purchase_return_recognition_event_id
            )
        ]

        adjusted_tax = round_currency_amount(
            amount=_decimal(
                event.adjusted_tax_amount
            ),
            currency_code=currency,
        )

        totals[
            source_id
        ] = round_currency_amount(
            amount=(
                totals.get(
                    source_id,
                    ZERO,
                )
                + adjusted_tax
            ),
            currency_code=currency,
        )

    return totals



def _build_active_purchase_value_correction_vat_events(
    *,
    events: tuple[
        PurchaseValueCorrectionVatAdjustmentEvent,
        ...,
    ],
    currency_code: str,
) -> tuple[
    PurchaseValueCorrectionVatAdjustmentEvent,
    ...,
]:
    """
    Resolve immutable PVC economic VAT history.

    Only ACTIVE originals contribute current supplier 631 truth.

    Reversal:
        removes its immutable original from active state.

    Replacement:
        is a new independent original.

    This helper performs no DB access and no mutation.
    """

    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(currency_code) != 3
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "PVC VAT currency must contain exactly 3 characters"
            )
        )

    event_by_id = {}

    for event in events:
        if not isinstance(
            event,
            PurchaseValueCorrectionVatAdjustmentEvent,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT history row has invalid type"
                )
            )

        event_id = event.id

        if (
            not isinstance(
                event_id,
                int,
            )
            or isinstance(
                event_id,
                bool,
            )
            or event_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT event ID must be greater than zero"
                )
            )

        if event_id in event_by_id:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Duplicate PVC VAT event ID"
                )
            )

        if (
            not isinstance(
                event.tax_calculation_id,
                int,
            )
            or isinstance(
                event.tax_calculation_id,
                bool,
            )
            or event.tax_calculation_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT tax_calculation_id "
                    "must be greater than zero"
                )
            )

        if (
            not isinstance(
                event.trade_value_correction_event_id,
                int,
            )
            or isinstance(
                event.trade_value_correction_event_id,
                bool,
            )
            or event.trade_value_correction_event_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT TradeValueCorrectionEvent ID "
                    "must be greater than zero"
                )
            )

        if event.adjustment_kind not in {
            "decrease",
            "increase",
        }:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT adjustment_kind is invalid"
                )
            )

        if event.currency_code != currency_code:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT currency mismatch"
                )
            )

        try:
            taxable_base = Decimal(
                event.adjusted_taxable_base
            )
            tax_amount = Decimal(
                event.adjusted_tax_amount
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT monetary state is not numeric"
                )
            ) from exc

        if (
            not taxable_base.is_finite()
            or not tax_amount.is_finite()
            or taxable_base < ZERO
            or tax_amount < ZERO
            or (
                taxable_base == ZERO
                and tax_amount == ZERO
            )
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT event contains invalid monetary state"
                )
            )

        if not isinstance(
            event.adjustment_date,
            date,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT adjustment_date must be a date"
                )
            )

        event_by_id[
            event_id
        ] = event

    reversed_original_ids = set()

    for event_id, event in event_by_id.items():
        if event.reversal_of_id is None:
            continue

        reversal_of_id = event.reversal_of_id

        if (
            not isinstance(
                reversal_of_id,
                int,
            )
            or isinstance(
                reversal_of_id,
                bool,
            )
            or reversal_of_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT reversal_of_id "
                    "must be greater than zero"
                )
            )

        original = event_by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT reversal references unloaded original"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT reversal cannot reverse another reversal"
                )
            )

        if reversal_of_id in reversed_original_ids:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT original has multiple reversals"
                )
            )

        if (
            event.trade_value_correction_event_id
            != original.trade_value_correction_event_id
            or event.tax_calculation_id
            != original.tax_calculation_id
            or event.adjustment_kind
            != original.adjustment_kind
            or Decimal(
                event.adjusted_taxable_base
            )
            != Decimal(
                original.adjusted_taxable_base
            )
            or Decimal(
                event.adjusted_tax_amount
            )
            != Decimal(
                original.adjusted_tax_amount
            )
            or event.currency_code
            != original.currency_code
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT reversal changed immutable "
                    "source or monetary state"
                )
            )

        if (
            event.adjustment_date
            < original.adjustment_date
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT reversal predates its original"
                )
            )

        reversed_original_ids.add(
            reversal_of_id
        )

    active = tuple(
        event
        for event_id, event
        in event_by_id.items()
        if (
            event.reversal_of_id is None
            and event_id
            not in reversed_original_ids
        )
    )

    active_keys = set()

    for event in active:
        key = (
            event.trade_value_correction_event_id,
            event.tax_calculation_id,
            event.adjustment_kind,
        )

        if key in active_keys:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT source has multiple active originals"
                )
            )

        active_keys.add(
            key
        )

    return tuple(
        sorted(
            active,
            key=lambda event: (
                event.adjustment_date,
                event.tax_calculation_id,
                event.id,
            ),
        )
    )


async def _load_active_purchase_value_correction_vat_events(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_ids: set[int],
    currency_code: str,
) -> tuple[
    PurchaseValueCorrectionVatAdjustmentEvent,
    ...,
]:
    """
    Lock PVC economic VAT history for the current invoice's
    INPUT VAT TaxCalculation sources and rebuild ACTIVE state.
    """

    if not tax_calculation_ids:
        return ()

    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionVatAdjustmentEvent
            )
            .where(
                (
                    PurchaseValueCorrectionVatAdjustmentEvent
                    .company_id
                    == company_id
                ),
                (
                    PurchaseValueCorrectionVatAdjustmentEvent
                    .tax_calculation_id
                    .in_(
                        tuple(
                            sorted(
                                tax_calculation_ids
                            )
                        )
                    )
                ),
            )
            .order_by(
                PurchaseValueCorrectionVatAdjustmentEvent.id
            )
            .with_for_update()
        )
    ).scalars().all()

    return (
        _build_active_purchase_value_correction_vat_events(
            events=tuple(
                rows
            ),
            currency_code=currency_code,
        )
    )


async def _apply_purchase_value_correction_vat_to_supplier_components(
    db: AsyncSession,
    *,
    company_id: int,
    vat_components: tuple[
        SupplierVatLiabilityComponent,
        ...,
    ],
    all_invoice_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
    active_invoice_source_ids: set[int],
    currency_code: str,
) -> tuple[
    SupplierVatLiabilityComponent,
    ...,
]:
    """
    Overlay ACTIVE economic PVC VAT onto current supplier VAT
    liability components.

    Critical provenance invariant:

        source_id  = original InvoiceFulfillmentAllocation
        event_date = original economic VAT / receipt source date

    PVC changes current monetary capacity only.

    It MUST NOT move the supplier liability source date to the
    later PVC/RK adjustment date.

    Legal INPUT VAT credit correction 644/641 is deliberately
    outside this function.
    """

    if not vat_components:
        return ()

    component_by_source = {}

    for component in vat_components:
        if not isinstance(
            component,
            SupplierVatLiabilityComponent,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT component has invalid type"
                )
            )

        if component.source_id in component_by_source:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Supplier VAT components contain duplicate "
                    "IFA source IDs"
                )
            )

        component_by_source[
            component.source_id
        ] = component

    all_source_ids = tuple(
        allocation.id
        for allocation
        in all_invoice_allocations
    )

    if not all_source_ids:
        return vat_components

    bridge_rows = tuple(
        (
            await db.execute(
                select(
                    InputVatFulfillmentBridgeEvent
                )
                .where(
                    (
                        InputVatFulfillmentBridgeEvent
                        .company_id
                        == company_id
                    ),
                    (
                        InputVatFulfillmentBridgeEvent
                        .invoice_fulfillment_allocation_id
                        .in_(
                            all_source_ids
                        )
                    ),
                )
                .order_by(
                    InputVatFulfillmentBridgeEvent
                    .tax_calculation_id,
                    InputVatFulfillmentBridgeEvent.id,
                )
            )
        )
        .scalars()
        .all()
    )

    if not bridge_rows:
        return vat_components

    bridge_rows_by_calculation = {}

    for row in bridge_rows:
        bridge_rows_by_calculation.setdefault(
            row.tax_calculation_id,
            [],
        ).append(
            row
        )

    targets_by_calculation = {}

    for calculation_id in sorted(
        bridge_rows_by_calculation
    ):
        try:
            current_targets = (
                build_current_input_vat_fulfillment_bridge_targets(
                    events=tuple(
                        bridge_rows_by_calculation[
                            calculation_id
                        ]
                    ),
                    currency_code=currency_code,
                )
            )
        except (
            InputVatFulfillmentBridgeDataIntegrityError
        ) as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Could not rebuild INPUT VAT bridge "
                    "for PVC VAT allocation: "
                    f"{exc}"
                )
            ) from exc

        for target in current_targets:
            if (
                target.source_id
                not in active_invoice_source_ids
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "PVC VAT allocation encountered "
                        "ACTIVE INPUT VAT bridge for "
                        "non-ACTIVE InvoiceFulfillmentAllocation"
                    )
                )

            if (
                target.source_id
                not in component_by_source
            ):
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "PVC VAT bridge source has no current "
                        "supplier VAT liability component"
                    )
                )

        targets_by_calculation[
            calculation_id
        ] = tuple(
            current_targets
        )

    active_vat_events = (
        await _load_active_purchase_value_correction_vat_events(
            db,
            company_id=company_id,
            tax_calculation_ids=set(
                targets_by_calculation
            ),
            currency_code=currency_code,
        )
    )

    if not active_vat_events:
        return vat_components

    amount_by_source = {
        source_id: Decimal(
            component.amount
        )
        for source_id, component
        in component_by_source.items()
    }

    historical_date_by_source = {
        source_id: component.event_date
        for source_id, component
        in component_by_source.items()
    }

    for event in active_vat_events:
        targets = (
            targets_by_calculation.get(
                event.tax_calculation_id,
                (),
            )
        )

        if not targets:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "ACTIVE PVC VAT event has no current "
                    "INPUT VAT fulfillment provenance"
                )
            )

        candidates = []

        for target in targets:
            source_id = target.source_id

            current_amount = amount_by_source[
                source_id
            ]

            if current_amount <= ZERO:
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "PVC VAT nonzero correction has no "
                        "positive current source VAT capacity"
                    )
                )

            candidates.append(
                SupplierVatAllocationCandidate(
                    source_id=source_id,
                    event_date=(
                        historical_date_by_source[
                            source_id
                        ]
                    ),
                    amount=current_amount,
                )
            )

        try:
            adjustments = (
                build_purchase_value_correction_supplier_vat_adjustments(
                    components=tuple(
                        candidates
                    ),
                    adjustment_kind=(
                        event.adjustment_kind
                    ),
                    adjusted_tax_amount=(
                        event.adjusted_tax_amount
                    ),
                )
            )
        except (
            PurchaseValueCorrectionSupplierVatLiabilityCalculationError
        ) as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "PVC VAT supplier-liability allocation "
                    f"failed: {exc}"
                )
            ) from exc

        for adjustment in adjustments:
            source_id = adjustment.source_id

            current = amount_by_source[
                source_id
            ]

            corrected = (
                current
                + Decimal(
                    adjustment.amount_delta
                )
            )

            if corrected < ZERO:
                raise (
                    SupplierAdvanceClearingReconciliationDataIntegrityError(
                        "PVC VAT produced negative supplier "
                        "VAT liability capacity"
                    )
                )

            amount_by_source[
                source_id
            ] = corrected

    result = []

    for source_id in sorted(
        component_by_source,
        key=lambda value: (
            historical_date_by_source[
                value
            ],
            value,
        ),
    ):
        amount = amount_by_source[
            source_id
        ]

        if amount == ZERO:
            continue

        result.append(
            SupplierVatLiabilityComponent(
                source_id=source_id,
                event_date=(
                    historical_date_by_source[
                        source_id
                    ]
                ),
                amount=amount,
            )
        )

    return tuple(
        result
    )


def _build_active_purchase_value_correction_base_adjustments(
    *,
    events: tuple[
        PurchaseValueCorrectionAllocationEvent,
        ...,
    ],
    active_source_ids: set[int],
    currency_code: str,
) -> tuple[
    SupplierEconomicLiabilityBaseAdjustment,
    ...,
]:
    """
    Resolve immutable PurchaseValueCorrectionAllocationEvent history
    into ACTIVE signed VAT-exclusive 631 base adjustments.

    One active original contributes:

        corrected_allocated_base_amount
        - original_allocated_base_amount

    Multiple independent TradeValueCorrectionEvent sources may be
    active for the same InvoiceFulfillmentAllocation and therefore
    remain separate adjustments here. The pure invoice-net builder
    performs their monetary aggregation.

    Reversal rows never contribute economic capacity directly.
    Historical rows are never updated or deleted.
    """

    if not active_source_ids:
        return ()

    normalized_source_ids = set()

    for source_id in active_source_ids:
        if (
            not isinstance(
                source_id,
                int,
            )
            or isinstance(
                source_id,
                bool,
            )
            or source_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Value Correction "
                    "source ID must be greater than zero"
                )
            )

        normalized_source_ids.add(
            source_id
        )

    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(
            currency_code
        )
        != 3
    ):
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Value Correction currency "
                "must contain exactly 3 characters"
            )
        )

    event_by_id = {}

    normalized = {}

    for event in events:
        if not isinstance(
            event,
            PurchaseValueCorrectionAllocationEvent,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction history row "
                    "has invalid type"
                )
            )

        event_id = event.id

        if (
            not isinstance(
                event_id,
                int,
            )
            or isinstance(
                event_id,
                bool,
            )
            or event_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "event ID must be greater than zero"
                )
            )

        if event_id in event_by_id:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Duplicate Purchase Value Correction "
                    "allocation event ID"
                )
            )

        source_id = (
            event
            .invoice_fulfillment_allocation_id
        )

        if (
            not isinstance(
                source_id,
                int,
            )
            or isinstance(
                source_id,
                bool,
            )
            or source_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "has invalid IFA source ID"
                )
            )

        if (
            source_id
            not in normalized_source_ids
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "references a non-active "
                    "InvoiceFulfillmentAllocation"
                )
            )

        correction_id = (
            event
            .trade_value_correction_event_id
        )

        if (
            not isinstance(
                correction_id,
                int,
            )
            or isinstance(
                correction_id,
                bool,
            )
            or correction_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "has invalid correction source ID"
                )
            )

        if not isinstance(
            event.recognition_date,
            date,
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction "
                    "recognition_date must be a date"
                )
            )

        try:
            original = Decimal(
                event
                .original_allocated_base_amount
            )

            corrected = Decimal(
                event
                .corrected_allocated_base_amount
            )
        except Exception as exc:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "amount is not numeric"
                )
            ) from exc

        if (
            not original.is_finite()
            or not corrected.is_finite()
            or original < 0
            or corrected < 0
            or original == corrected
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "contains invalid monetary state"
                )
            )

        if (
            event.currency_code
            != currency_code
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "currency mismatch"
                )
            )

        event_by_id[
            event_id
        ] = event

        normalized[
            event_id
        ] = (
            source_id,
            correction_id,
            original,
            corrected,
        )

    reversed_original_ids = set()

    for event_id, event in event_by_id.items():
        if event.reversal_of_id is None:
            continue

        reversal_of_id = (
            event.reversal_of_id
        )

        if (
            not isinstance(
                reversal_of_id,
                int,
            )
            or isinstance(
                reversal_of_id,
                bool,
            )
            or reversal_of_id <= 0
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal_of_id "
                    "must be greater than zero"
                )
            )

        original_event = (
            event_by_id.get(
                reversal_of_id
            )
        )

        if original_event is None:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal "
                    "references an unloaded original"
                )
            )

        if (
            original_event.reversal_of_id
            is not None
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal "
                    "cannot reverse another reversal"
                )
            )

        if (
            reversal_of_id
            in reversed_original_ids
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction original "
                    "has multiple reversals"
                )
            )

        (
            reversal_source_id,
            reversal_correction_id,
            reversal_original,
            reversal_corrected,
        ) = normalized[
            event_id
        ]

        (
            original_source_id,
            original_correction_id,
            original_original,
            original_corrected,
        ) = normalized[
            reversal_of_id
        ]

        if (
            reversal_source_id
            != original_source_id
            or reversal_correction_id
            != original_correction_id
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal "
                    "changed source provenance"
                )
            )

        if (
            reversal_original
            != original_original
            or reversal_corrected
            != original_corrected
            or event.currency_code
            != original_event.currency_code
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal "
                    "changed immutable monetary state"
                )
            )

        if (
            event.recognition_date
            < original_event.recognition_date
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Purchase Value Correction reversal "
                    "predates its original"
                )
            )

        reversed_original_ids.add(
            reversal_of_id
        )

    active_originals = tuple(
        event
        for event_id, event
        in event_by_id.items()
        if (
            event.reversal_of_id
            is None
            and event_id
            not in reversed_original_ids
        )
    )

    active_pair_counts = {}

    for event in active_originals:
        key = (
            event
            .trade_value_correction_event_id,
            event
            .invoice_fulfillment_allocation_id,
        )

        active_pair_counts[
            key
        ] = (
            active_pair_counts.get(
                key,
                0,
            )
            + 1
        )

    duplicates = tuple(
        key
        for key, count
        in active_pair_counts.items()
        if count > 1
    )

    if duplicates:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Purchase Value Correction source pair "
                "has multiple active originals"
            )
        )

    ordered = tuple(
        sorted(
            active_originals,
            key=lambda event: (
                event.recognition_date,
                (
                    event
                    .invoice_fulfillment_allocation_id
                ),
                event.id,
            ),
        )
    )

    adjustments = []

    for event in ordered:
        (
            source_id,
            _,
            original,
            corrected,
        ) = normalized[
            event.id
        ]

        delta = (
            corrected
            - original
        )

        if delta == 0:
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Active Purchase Value Correction "
                    "allocation cannot be a no-op"
                )
            )

        adjustments.append(
            SupplierEconomicLiabilityBaseAdjustment(
                source_id=source_id,
                recognition_date=(
                    event.recognition_date
                ),
                base_delta=delta,
                currency_code=(
                    event.currency_code
                ),
            )
        )

    return tuple(
        adjustments
    )


async def _load_active_purchase_value_correction_base_adjustments(
    db: AsyncSession,
    *,
    company_id: int,
    active_source_ids: set[int],
    currency_code: str,
) -> tuple[
    SupplierEconomicLiabilityBaseAdjustment,
    ...,
]:
    """
    Lock and rebuild ACTIVE Purchase Value Correction base deltas for
    current supplier 631 truth.

    Only correction allocations attached to this invoice's ACTIVE
    InvoiceFulfillmentAllocation source IDs are eligible.
    """

    if not active_source_ids:
        return ()

    source_ids = tuple(
        sorted(
            active_source_ids
        )
    )

    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .where(
                (
                    PurchaseValueCorrectionAllocationEvent
                    .company_id
                    == company_id
                ),
                (
                    PurchaseValueCorrectionAllocationEvent
                    .invoice_fulfillment_allocation_id
                    .in_(
                        source_ids
                    )
                ),
            )
            .order_by(
                PurchaseValueCorrectionAllocationEvent.id
            )
            .with_for_update()
        )
    ).scalars().all()

    return (
        _build_active_purchase_value_correction_base_adjustments(
            events=tuple(
                rows
            ),
            active_source_ids=set(
                source_ids
            ),
            currency_code=currency_code,
        )
    )


async def _load_supplier_economic_liability_candidates(
    db: AsyncSession,
    *,
    invoice: TradeDocument,
    all_invoice_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
    currency_code: str,
) -> tuple[
    SupplierEconomicLiabilityCandidate,
    ...,
]:
    active_invoice_allocations = tuple(
        allocation
        for allocation
        in all_invoice_allocations
        if (
            allocation.status
            == InvoiceFulfillmentAllocationStatus.ACTIVE
        )
    )

    if not active_invoice_allocations:
        return ()

    active_source_ids = {
        allocation.id
        for allocation
        in active_invoice_allocations
    }

    peers = (
        await _load_receipt_peer_snapshots(
            db,
            company_id=invoice.company_id,
            active_invoice_allocations=(
                active_invoice_allocations
            ),
        )
    )

    base_targets = (
        build_supplier_receipt_base_targets_for_invoice(
            peers=peers,
            invoice_source_ids=tuple(
                sorted(
                    active_source_ids
                )
            ),
            currency_code=currency_code,
        )
    )

    active_return_base_by_source = (
        await _load_active_purchase_return_base_by_source(
            db,
            company_id=invoice.company_id,
            active_source_ids=(
                active_source_ids
            ),
            currency_code=currency_code,
        )
    )

    base_targets = (
        apply_purchase_return_base_to_supplier_receipt_targets(
            base_targets=base_targets,
            active_return_base_by_source=(
                active_return_base_by_source
            ),
            currency_code=currency_code,
        )
    )

    vat_components = (
        await _load_supplier_vat_components(
            db,
            company_id=invoice.company_id,
            all_invoice_allocations=(
                all_invoice_allocations
            ),
            active_invoice_source_ids=(
                active_source_ids
            ),
            currency_code=currency_code,
        )
    )

    active_return_vat_by_source = (
        await _load_active_purchase_return_vat_by_source(
            db,
            company_id=invoice.company_id,
            active_source_ids=active_source_ids,
            currency_code=currency_code,
        )
    )

    vat_components = (
        apply_purchase_return_vat_to_supplier_vat_components(
            vat_components=vat_components,
            active_return_vat_by_source=(
                active_return_vat_by_source
            ),
            currency_code=currency_code,
        )
    )

    vat_components = (
        await _apply_purchase_value_correction_vat_to_supplier_components(
            db,
            company_id=invoice.company_id,
            vat_components=vat_components,
            all_invoice_allocations=(
                all_invoice_allocations
            ),
            active_invoice_source_ids=(
                active_source_ids
            ),
            currency_code=currency_code,
        )
    )

    base_adjustments = (
        await _load_active_purchase_value_correction_base_adjustments(
            db,
            company_id=invoice.company_id,
            active_source_ids=(
                active_source_ids
            ),
            currency_code=currency_code,
        )
    )

    try:
        return (
            build_supplier_economic_liability_candidates_with_base_adjustments(
                base_targets=base_targets,
                vat_components=vat_components,
                base_adjustments=base_adjustments,
                currency_code=currency_code,
            )
        )
    except (
        SupplierEconomicLiabilityCalculationError
    ) as exc:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier economic liability "
                f"calculation failed: {exc}"
            )
        ) from exc


async def _load_supplier_clearing_history(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_id: int,
) -> tuple[
    SupplierAdvanceClearingEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    SupplierAdvanceClearingEvent
                )
                .join(
                    PaymentSettlementAllocation,
                    and_(
                        (
                            PaymentSettlementAllocation
                            .company_id
                            == (
                                SupplierAdvanceClearingEvent
                                .company_id
                            )
                        ),
                        (
                            PaymentSettlementAllocation.id
                            == (
                                SupplierAdvanceClearingEvent
                                .payment_settlement_allocation_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        SupplierAdvanceClearingEvent
                        .company_id
                        == company_id
                    ),
                    (
                        PaymentSettlementAllocation
                        .open_item_id
                        == open_item_id
                    ),
                )
                .order_by(
                    SupplierAdvanceClearingEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


def _validate_current_liability_sources(
    *,
    current_targets: tuple[
        SupplierAdvanceClearingTarget,
        ...,
    ],
    all_invoice_allocations: tuple[
        InvoiceFulfillmentAllocation,
        ...,
    ],
) -> None:
    valid_ids = {
        allocation.id
        for allocation
        in all_invoice_allocations
    }

    for target in current_targets:
        if (
            target.liability_source_id
            not in valid_ids
        ):
            raise (
                SupplierAdvanceClearingReconciliationDataIntegrityError(
                    "Persistent supplier clearing "
                    "event links this invoice's "
                    "settlement to another invoice's "
                    "fulfillment source"
                )
            )


async def reconcile_supplier_advance_clearing_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> SupplierAdvanceClearingReconciliationResult:
    """
    Reconcile complete supplier-advance clearing state
    for one Purchase Invoice.

    Commercial capacity:
        ACTIVE PaymentSettlementAllocation
        -> PAYABLE CounterpartyOpenItem
        -> CONFIRMED OUTGOING Payment.

    Economic 631 capacity:
        ACTIVE InvoiceFulfillmentAllocation
        -> POSTED warehouse RECEIPT accounting base
        - ACTIVE PurchaseReturnRecognitionEvent returned base
        + ACTIVE INPUT VAT economic bridge.
        - ACTIVE economic Purchase Return VAT adjustment.
        + ACTIVE Purchase Value Correction signed base delta.

    Desired accounting:
        Dr SUPPLIER_PAYABLES
        Cr SUPPLIER_ADVANCES

        GENERAL 291:
        Dr 631
        Cr 371

    Existing immutable pairs that disappear from desired
    state are reconciled to zero.

    Caller owns COMMIT / ROLLBACK.
    """

    if (
        not isinstance(
            company_id,
            int,
        )
        or isinstance(
            company_id,
            bool,
        )
        or company_id <= 0
    ):
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        not isinstance(
            invoice_id,
            int,
        )
        or isinstance(
            invoice_id,
            bool,
        )
        or invoice_id <= 0
    ):
        raise ValueError(
            "invoice_id must be greater than zero"
        )

    if (
        not isinstance(
            created_by,
            int,
        )
        or isinstance(
            created_by,
            bool,
        )
        or created_by <= 0
    ):
        raise ValueError(
            "created_by must be greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    invoice = (
        await _lock_purchase_invoice(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    currency_code = (
        _validate_purchase_invoice(
            invoice,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    open_item = (
        await _load_purchase_open_item(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    _validate_open_item(
        open_item,
        invoice=invoice,
        currency_code=currency_code,
    )

    settlement_candidates = (
        await _load_supplier_settlement_candidates(
            db,
            invoice=invoice,
            open_item=open_item,
            currency_code=currency_code,
        )
    )

    all_invoice_allocations = (
        await _load_invoice_fulfillment_allocations(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    liability_candidates = (
        await _load_supplier_economic_liability_candidates(
            db,
            invoice=invoice,
            all_invoice_allocations=(
                all_invoice_allocations
            ),
            currency_code=currency_code,
        )
    )

    try:
        desired_targets = (
            build_supplier_advance_clearing_targets(
                settlements=(
                    settlement_candidates
                ),
                liabilities=(
                    liability_candidates
                ),
                currency_code=currency_code,
            )
        )
    except (
        SupplierAdvanceClearingCalculationError
    ) as exc:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Supplier advance clearing "
                f"calculation failed: {exc}"
            )
        ) from exc

    history = (
        await _load_supplier_clearing_history(
            db,
            company_id=company_id,
            open_item_id=open_item.id,
        )
    )

    try:
        current_targets = (
            build_current_supplier_advance_clearing_targets(
                events=history,
                currency_code=currency_code,
            )
        )
    except (
        SupplierAdvanceClearingDataIntegrityError
    ) as exc:
        raise (
            SupplierAdvanceClearingReconciliationDataIntegrityError(
                "Could not rebuild current supplier "
                f"clearing state: {exc}"
            )
        ) from exc

    _validate_current_liability_sources(
        current_targets=current_targets,
        all_invoice_allocations=(
            all_invoice_allocations
        ),
    )

    reconciliation_targets = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=desired_targets,
            current_targets=current_targets,
        )
    )

    created_events = []

    for target in reconciliation_targets:
        created_events.extend(
            await reconcile_supplier_advance_clearing_source(
                db,
                company_id=company_id,
                target=target,
                currency_code=currency_code,
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

    return (
        SupplierAdvanceClearingReconciliationResult(
            invoice_id=invoice_id,
            settlement_candidates=(
                settlement_candidates
            ),
            liability_candidates=(
                liability_candidates
            ),
            current_targets=(
                current_targets
            ),
            desired_targets=(
                desired_targets
            ),
            reconciliation_targets=(
                reconciliation_targets
            ),
            created_events=tuple(
                created_events
            ),
        )
    )
