from dataclasses import (
    dataclass,
    replace,
)
from datetime import (
    date,
    datetime,
)
from decimal import (
    Decimal,
    InvalidOperation,
)

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.purchase_value_correction_allocation_calculation_service import (
    PurchaseValueCorrectionAllocationCandidate,
    PurchaseValueCorrectionAllocationTarget,
    build_purchase_value_correction_allocation_targets,
)
from app.services.purchase_value_correction_allocation_persistence_service import (
    PurchaseValueCorrectionAllocationPersistenceError,
    reconcile_purchase_value_correction_allocation_source,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionAllocationReconciliationError(
    Exception
):
    """Base Purchase Value Correction DB reconciliation error."""


class PurchaseValueCorrectionAllocationReconciliationNotFoundError(
    PurchaseValueCorrectionAllocationReconciliationError
):
    """Required correction or economic source was not found."""


class PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
    PurchaseValueCorrectionAllocationReconciliationError
):
    """Immutable Purchase Value Correction state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class _RequestedCorrectionState:
    requested_event: TradeValueCorrectionEvent
    source_event: TradeValueCorrectionEvent
    is_active: bool


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionAllocationReconciliationResult:
    requested_event_id: int
    source_event_id: int
    source_is_active: bool

    desired_targets: tuple[
        PurchaseValueCorrectionAllocationTarget,
        ...,
    ]

    reconciliation_targets: tuple[
        PurchaseValueCorrectionAllocationTarget,
        ...,
    ]

    created_events: tuple[
        PurchaseValueCorrectionAllocationEvent,
        ...,
    ]


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if (
        not isinstance(
            value,
            date,
        )
        or isinstance(
            value,
            datetime,
        )
    ):
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    if isinstance(
        value,
        bool,
    ):
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                f"{field} must be numeric"
            )
        )

    try:
        result = Decimal(
            value
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                f"{field} must be numeric"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _event_id(
    event,
    *,
    field: str,
) -> int:
    return _positive_id(
        getattr(
            event,
            "id",
            None,
        ),
        field=field,
    )


def _validate_reversal_provenance(
    *,
    original: TradeValueCorrectionEvent,
    reversal: TradeValueCorrectionEvent,
) -> None:
    if reversal.reversal_of_id != original.id:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "TradeValueCorrectionEvent reversal "
                "does not reference expected original"
            )
        )

    if original.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "TradeValueCorrectionEvent reversal "
                "cannot reverse another reversal"
            )
        )

    immutable_fields = (
        "company_id",
        "direction",
        "trade_document_id",
        "trade_document_line_id",
        "product_id",
        "original_gross_amount",
        "original_tax_amount",
        "corrected_gross_amount",
        "corrected_tax_amount",
        "currency_code",
    )

    for field in immutable_fields:
        if (
            getattr(
                reversal,
                field,
            )
            != getattr(
                original,
                field,
            )
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "TradeValueCorrectionEvent reversal "
                    "does not preserve immutable "
                    f"source state: {field}"
                )
            )


async def _load_requested_correction_state(
    db: AsyncSession,
    *,
    company_id: int,
    requested_event_id: int,
) -> _RequestedCorrectionState:
    requested = (
        await db.execute(
            select(
                TradeValueCorrectionEvent
            )
            .where(
                TradeValueCorrectionEvent.company_id
                == company_id,
                TradeValueCorrectionEvent.id
                == requested_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if requested is None:
        raise (
            PurchaseValueCorrectionAllocationReconciliationNotFoundError(
                "TradeValueCorrectionEvent was not found"
            )
        )

    if requested.direction != "purchase":
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "Purchase Value Correction reconciliation "
                "requires direction='purchase'"
            )
        )

    if requested.reversal_of_id is not None:
        original = (
            await db.execute(
                select(
                    TradeValueCorrectionEvent
                )
                .where(
                    TradeValueCorrectionEvent.company_id
                    == company_id,
                    TradeValueCorrectionEvent.id
                    == requested.reversal_of_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if original is None:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "TradeValueCorrectionEvent reversal "
                    "references missing original"
                )
            )

        _validate_reversal_provenance(
            original=original,
            reversal=requested,
        )

        return _RequestedCorrectionState(
            requested_event=requested,
            source_event=original,
            is_active=False,
        )

    reversals = tuple(
        (
            await db.execute(
                select(
                    TradeValueCorrectionEvent
                )
                .where(
                    TradeValueCorrectionEvent.company_id
                    == company_id,
                    TradeValueCorrectionEvent.reversal_of_id
                    == requested.id,
                )
                .order_by(
                    TradeValueCorrectionEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    if len(
        reversals
    ) > 1:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "TradeValueCorrectionEvent original "
                "has multiple reversal rows"
            )
        )

    if reversals:
        _validate_reversal_provenance(
            original=requested,
            reversal=reversals[
                0
            ],
        )

    return _RequestedCorrectionState(
        requested_event=requested,
        source_event=requested,
        is_active=not reversals,
    )


async def _load_invoice_line_quantity(
    db: AsyncSession,
    *,
    company_id: int,
    correction: TradeValueCorrectionEvent,
) -> Decimal:
    line = (
        await db.execute(
            select(
                TradeDocumentLine
            )
            .where(
                TradeDocumentLine.company_id
                == company_id,
                TradeDocumentLine.trade_document_id
                == correction.trade_document_id,
                TradeDocumentLine.id
                == correction.trade_document_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise (
            PurchaseValueCorrectionAllocationReconciliationNotFoundError(
                "Purchase invoice line for "
                "TradeValueCorrectionEvent was not found"
            )
        )

    if line.product_id != correction.product_id:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "TradeValueCorrectionEvent product "
                "does not match purchase invoice line"
            )
        )

    quantity = _decimal(
        line.quantity,
        field="invoice line quantity",
    )

    if quantity <= ZERO:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "Purchase invoice line quantity "
                "must be positive"
            )
        )

    return quantity


async def _load_active_allocation_candidates(
    db: AsyncSession,
    *,
    company_id: int,
    correction: TradeValueCorrectionEvent,
) -> tuple[
    PurchaseValueCorrectionAllocationCandidate,
    ...,
]:
    rows = tuple(
        (
            await db.execute(
                select(
                    InvoiceFulfillmentAllocation,
                    TradeFulfillmentLine,
                    Document,
                )
                .join(
                    TradeFulfillmentLine,
                    and_(
                        TradeFulfillmentLine.company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        ),
                        TradeFulfillmentLine.fulfillment_id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_id
                        ),
                        TradeFulfillmentLine.id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_line_id
                        ),
                    ),
                )
                .join(
                    Document,
                    and_(
                        Document.company_id
                        == (
                            TradeFulfillmentLine
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
                        .invoice_id
                        == correction.trade_document_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .invoice_line_id
                        == correction.trade_document_line_id
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
                    Document.document_date,
                    InvoiceFulfillmentAllocation.id,
                )
                .with_for_update()
            )
        ).all()
    )

    candidates = []
    seen_source_ids = set()

    for (
        allocation,
        fulfillment_line,
        receipt_document,
    ) in rows:
        source_id = _event_id(
            allocation,
            field=(
                "InvoiceFulfillmentAllocation.id"
            ),
        )

        if source_id in seen_source_ids:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "ACTIVE InvoiceFulfillmentAllocation "
                    "appears more than once in receipt peers"
                )
            )

        seen_source_ids.add(
            source_id
        )

        if (
            allocation.product_id
            != correction.product_id
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "ACTIVE fulfillment allocation "
                    "product does not match correction"
                )
            )

        if (
            fulfillment_line.fulfillment_id
            != allocation.fulfillment_id
            or fulfillment_line.id
            != allocation.fulfillment_line_id
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Fulfillment-line provenance "
                    "does not match allocation"
                )
            )

        if (
            receipt_document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "requires warehouse RECEIPT provenance"
                )
            )

        if (
            receipt_document.status
            != DocumentStatus.POSTED
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Purchase Value Correction allocation "
                    "requires POSTED warehouse receipt"
                )
            )

        receipt_date = _business_date(
            receipt_document.document_date,
            field="receipt document_date",
        )

        quantity = _decimal(
            allocation.quantity,
            field="allocation quantity",
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "ACTIVE allocation quantity "
                    "must be positive"
                )
            )

        candidates.append(
            PurchaseValueCorrectionAllocationCandidate(
                invoice_fulfillment_allocation_id=(
                    source_id
                ),
                receipt_event_date=(
                    receipt_date
                ),
                quantity=quantity,
            )
        )

    return tuple(
        candidates
    )


async def _load_allocation_history(
    db: AsyncSession,
    *,
    company_id: int,
    source_event_id: int,
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    return tuple(
        (
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
                        .trade_value_correction_event_id
                        == source_event_id
                    ),
                )
                .order_by(
                    PurchaseValueCorrectionAllocationEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _active_allocation_event_map(
    *,
    events: tuple[
        PurchaseValueCorrectionAllocationEvent,
        ...,
    ],
    source_event_id: int,
    currency_code: str,
) -> dict[
    int,
    PurchaseValueCorrectionAllocationEvent,
]:
    event_by_id = {}

    for event in events:
        event_id = _event_id(
            event,
            field=(
                "PurchaseValueCorrectionAllocationEvent.id"
            ),
        )

        if event_id in event_by_id:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Duplicate Purchase Value Correction "
                    "allocation event id"
                )
            )

        if (
            event.trade_value_correction_event_id
            != source_event_id
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Allocation history contains "
                    "wrong TradeValueCorrectionEvent"
                )
            )

        _positive_id(
            event.invoice_fulfillment_allocation_id,
            field=(
                "invoice_fulfillment_allocation_id"
            ),
        )

        _business_date(
            event.recognition_date,
            field=(
                "allocation event recognition_date"
            ),
        )

        original_base = _decimal(
            event.original_allocated_base_amount,
            field=(
                "allocation event original base"
            ),
        )

        corrected_base = _decimal(
            event.corrected_allocated_base_amount,
            field=(
                "allocation event corrected base"
            ),
        )

        if (
            original_base < ZERO
            or corrected_base < ZERO
            or original_base
            == corrected_base
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Persistent allocation event "
                    "contains invalid monetary state"
                )
            )

        if event.currency_code != currency_code:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Persistent allocation event "
                    "currency mismatch"
                )
            )

        event_by_id[
            event_id
        ] = event

    reversed_ids = set()

    for event in events:
        if event.reversal_of_id is None:
            continue

        reversal_of_id = _positive_id(
            event.reversal_of_id,
            field="reversal_of_id",
        )

        original = event_by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Allocation reversal references "
                    "event outside source history"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Allocation reversal cannot "
                    "reverse another reversal"
                )
            )

        if (
            event.invoice_fulfillment_allocation_id
            != original.invoice_fulfillment_allocation_id
        ):
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Allocation reversal changed "
                    "fulfillment source provenance"
                )
            )

        if reversal_of_id in reversed_ids:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Allocation original has "
                    "multiple reversal rows"
                )
            )

        reversed_ids.add(
            reversal_of_id
        )

    active = {}

    for event in events:
        if event.reversal_of_id is not None:
            continue

        if event.id in reversed_ids:
            continue

        source_id = (
            event.invoice_fulfillment_allocation_id
        )

        if source_id in active:
            raise (
                PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                    "Correction allocation source has "
                    "multiple active originals"
                )
            )

        active[
            source_id
        ] = event

    return active


def _same_monetary_state(
    *,
    current: PurchaseValueCorrectionAllocationEvent,
    desired: PurchaseValueCorrectionAllocationTarget,
) -> bool:
    return (
        _decimal(
            current.original_allocated_base_amount,
            field="current original base",
        )
        == desired.original_allocated_base_amount
        and _decimal(
            current.corrected_allocated_base_amount,
            field="current corrected base",
        )
        == desired.corrected_allocated_base_amount
        and current.currency_code
        == desired.currency_code
    )


def _forward_only_target(
    *,
    desired: PurchaseValueCorrectionAllocationTarget,
    current: (
        PurchaseValueCorrectionAllocationEvent
        | None
    ),
    adjustment_date: date,
) -> PurchaseValueCorrectionAllocationTarget:
    """
    Date policy:

    New source:
        retain pure economic recognition_date.

    Existing source, same monetary state:
        retain CURRENT persisted recognition_date.
        Later reconciliation date alone must not churn history.

    Existing source, changed monetary state:
        immutable reversal/replacement occurs on adjustment_date.
    """

    if adjustment_date < desired.recognition_date:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "adjustment_date cannot precede "
                "desired economic recognition_date"
            )
        )

    if current is None:
        return desired

    current_date = _business_date(
        current.recognition_date,
        field="current recognition_date",
    )

    if _same_monetary_state(
        current=current,
        desired=desired,
    ):
        return replace(
            desired,
            recognition_date=current_date,
        )

    if adjustment_date < current_date:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "adjustment_date cannot precede "
                "current allocation recognition_date"
            )
        )

    return replace(
        desired,
        recognition_date=adjustment_date,
    )


def _zero_target(
    *,
    source_event_id: int,
    current: PurchaseValueCorrectionAllocationEvent,
) -> PurchaseValueCorrectionAllocationTarget:
    return (
        PurchaseValueCorrectionAllocationTarget(
            trade_value_correction_event_id=(
                source_event_id
            ),
            invoice_fulfillment_allocation_id=(
                current
                .invoice_fulfillment_allocation_id
            ),
            recognition_date=(
                current.recognition_date
            ),
            original_allocated_base_amount=ZERO,
            corrected_allocated_base_amount=ZERO,
            currency_code=(
                current.currency_code
            ),
        )
    )


async def reconcile_purchase_value_correction_allocations_for_event(
    db: AsyncSession,
    *,
    company_id: int,
    trade_value_correction_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionAllocationReconciliationResult:
    """
    Reconcile complete economic allocation state for one immutable
    TradeValueCorrectionEvent source.

    Scope:
        one original TradeValueCorrectionEvent
        across ALL currently ACTIVE
        InvoiceFulfillmentAllocation peers of its invoice line.

    Source lifecycle:
        ACTIVE correction original:
            calculate desired peer allocations.

        reversed correction original:
            all existing active allocation sources -> zero/reversal.

    Peer lifecycle:
        new future fulfillment:
            new source uses pure economic recognition_date.

        unchanged active peer:
            preserve historical recognition_date.

        changed peer due cumulative reallocation:
            reversal/replacement on caller adjustment_date.

        reversed/disappeared IFA:
            existing source -> zero/reversal on adjustment_date.

    Processing order:
        removals first,
        then desired ACTIVE peers in deterministic pure order.

    This layer does NOT:
        post JournalEntry,
        mutate inventory,
        reconcile supplier advances,
        perform VAT/RK,
        commit,
        rollback.

    Caller owns the transaction.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    requested_event_id = _positive_id(
        trade_value_correction_event_id,
        field=(
            "trade_value_correction_event_id"
        ),
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    adjustment_date = _business_date(
        adjustment_date,
        field="adjustment_date",
    )

    state = (
        await _load_requested_correction_state(
            db,
            company_id=company_id,
            requested_event_id=(
                requested_event_id
            ),
        )
    )

    requested_date = _business_date(
        state.requested_event.correction_date,
        field="requested correction_date",
    )

    source_date = _business_date(
        state.source_event.correction_date,
        field="source correction_date",
    )

    if adjustment_date < requested_date:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "adjustment_date cannot precede "
                "requested correction_date"
            )
        )

    if adjustment_date < source_date:
        raise (
            PurchaseValueCorrectionAllocationReconciliationDataIntegrityError(
                "adjustment_date cannot precede "
                "source correction_date"
            )
        )

    source_event_id = _event_id(
        state.source_event,
        field="source TradeValueCorrectionEvent.id",
    )

    history = (
        await _load_allocation_history(
            db,
            company_id=company_id,
            source_event_id=(
                source_event_id
            ),
        )
    )

    current_by_source = (
        _active_allocation_event_map(
            events=history,
            source_event_id=source_event_id,
            currency_code=(
                state.source_event.currency_code
            ),
        )
    )

    desired_targets = ()

    if state.is_active:
        invoice_line_quantity = (
            await _load_invoice_line_quantity(
                db,
                company_id=company_id,
                correction=state.source_event,
            )
        )

        candidates = (
            await _load_active_allocation_candidates(
                db,
                company_id=company_id,
                correction=state.source_event,
            )
        )

        desired_targets = (
            build_purchase_value_correction_allocation_targets(
                trade_value_correction_event_id=(
                    source_event_id
                ),
                correction_date=(
                    state.source_event.correction_date
                ),
                invoice_line_quantity=(
                    invoice_line_quantity
                ),
                original_gross_amount=(
                    state.source_event
                    .original_gross_amount
                ),
                original_tax_amount=(
                    state.source_event
                    .original_tax_amount
                ),
                corrected_gross_amount=(
                    state.source_event
                    .corrected_gross_amount
                ),
                corrected_tax_amount=(
                    state.source_event
                    .corrected_tax_amount
                ),
                currency_code=(
                    state.source_event.currency_code
                ),
                candidates=candidates,
            )
        )

    desired_source_ids = {
        target.invoice_fulfillment_allocation_id
        for target in desired_targets
    }

    current_source_ids = set(
        current_by_source
    )

    reconciliation_targets = []

    removed_source_ids = sorted(
        current_source_ids
        - desired_source_ids
    )

    for source_id in removed_source_ids:
        reconciliation_targets.append(
            _zero_target(
                source_event_id=(
                    source_event_id
                ),
                current=current_by_source[
                    source_id
                ],
            )
        )

    for desired in desired_targets:
        source_id = (
            desired
            .invoice_fulfillment_allocation_id
        )

        current = current_by_source.get(
            source_id
        )

        reconciled = _forward_only_target(
            desired=desired,
            current=current,
            adjustment_date=adjustment_date,
        )

        if (
            current is None
            and reconciled.is_noop
        ):
            continue

        reconciliation_targets.append(
            reconciled
        )

    created_events = []

    for target in reconciliation_targets:
        try:
            created = (
                await reconcile_purchase_value_correction_allocation_source(
                    db,
                    company_id=company_id,
                    target=target,
                    created_by=created_by,
                    reversal_date=adjustment_date,
                )
            )
        except (
            PurchaseValueCorrectionAllocationPersistenceError
        ) as exc:
            raise (
                PurchaseValueCorrectionAllocationReconciliationError(
                    "Purchase Value Correction allocation "
                    "persistence failed: "
                    f"{exc}"
                )
            ) from exc

        created_events.extend(
            created
        )

    return (
        PurchaseValueCorrectionAllocationReconciliationResult(
            requested_event_id=(
                requested_event_id
            ),
            source_event_id=(
                source_event_id
            ),
            source_is_active=(
                state.is_active
            ),
            desired_targets=tuple(
                desired_targets
            ),
            reconciliation_targets=tuple(
                reconciliation_targets
            ),
            created_events=tuple(
                created_events
            ),
        )
    )
