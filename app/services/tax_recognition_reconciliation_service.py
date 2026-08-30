from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.payment import (
    Payment,
)
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    TaxRecognitionCandidateKind,
    TaxRecognitionSourceTarget,
    build_fulfillment_recognition_candidate,
    build_output_tax_recognition_targets,
    build_settlement_recognition_candidate,
    order_output_tax_reconciliations,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionCalculationNotFoundError,
    TaxRecognitionDataIntegrityError,
    reconcile_output_tax_recognition_source,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)


ZERO = Decimal("0")


@dataclass(
    frozen=True,
    slots=True,
)
class OutputTaxRecognitionReconciliationResult:
    tax_calculation_id: int
    candidates: tuple[
        TaxRecognitionCandidate,
        ...,
    ]
    current_targets: tuple[
        TaxRecognitionSourceTarget,
        ...,
    ]
    desired_targets: tuple[
        TaxRecognitionSourceTarget,
        ...,
    ]
    adjustments: tuple[
        TaxRecognitionSourceTarget,
        ...,
    ]
    created_events: tuple[
        TaxRecognitionEvent,
        ...,
    ]


def _source_sort_key(
    target: TaxRecognitionSourceTarget,
) -> tuple[
    date,
    int,
    int,
]:
    kind_order = (
        0
        if (
            target.kind
            == TaxRecognitionCandidateKind
            .FULFILLMENT
        )
        else 1
    )

    return (
        target.event_date,
        kind_order,
        target.source_id,
    )


def _active_original_events(
    events: Iterable[
        TaxRecognitionEvent
    ],
) -> tuple[
    TaxRecognitionEvent,
    ...,
]:
    event_tuple = tuple(
        events
    )

    reversed_ids = {
        event.reversal_of_id
        for event in event_tuple
        if (
            event.reversal_of_id
            is not None
        )
    }

    active = []

    for event in event_tuple:
        if (
            event.reversal_of_id
            is not None
        ):
            continue

        if event.id is None:
            raise (
                TaxRecognitionDataIntegrityError(
                    "Persistent original recognition "
                    "event has no ID"
                )
            )

        if (
            event.id
            in reversed_ids
        ):
            continue

        active.append(
            event
        )

    return tuple(
        active
    )


def build_current_output_tax_recognition_targets(
    events: Iterable[
        TaxRecognitionEvent
    ],
) -> tuple[
    TaxRecognitionSourceTarget,
    ...,
]:
    active = (
        _active_original_events(
            events
        )
    )

    groups: dict[
        tuple[
            TaxRecognitionCandidateKind,
            int,
        ],
        list[TaxRecognitionEvent],
    ] = {}

    for event in active:
        fulfillment_id = (
            event
            .invoice_fulfillment_allocation_id
        )

        settlement_id = (
            event
            .payment_settlement_allocation_id
        )

        if (
            fulfillment_id is None
            and settlement_id is None
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "Automatic OUTPUT VAT ledger "
                    "contains a source-less active "
                    "recognition event"
                )
            )

        if (
            fulfillment_id is not None
            and settlement_id is not None
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "Recognition event has more "
                    "than one typed source"
                )
            )

        if fulfillment_id is not None:
            key = (
                TaxRecognitionCandidateKind
                .FULFILLMENT,
                fulfillment_id,
            )
        else:
            key = (
                TaxRecognitionCandidateKind
                .SETTLEMENT,
                settlement_id,
            )

        groups.setdefault(
            key,
            [],
        ).append(
            event
        )

    targets = []

    for (
        kind,
        source_id,
    ), source_events in groups.items():
        dates = {
            event.recognition_date
            for event in source_events
        }

        if len(dates) != 1:
            raise (
                TaxRecognitionDataIntegrityError(
                    "Active recognition increments "
                    "for one typed source have "
                    "different recognition dates"
                )
            )

        currencies = {
            event.currency_code
            for event in source_events
        }

        if len(currencies) != 1:
            raise (
                TaxRecognitionDataIntegrityError(
                    "Active recognition increments "
                    "for one typed source have "
                    "different currencies"
                )
            )

        targets.append(
            TaxRecognitionSourceTarget(
                kind=kind,
                source_id=source_id,
                event_date=next(
                    iter(
                        dates
                    )
                ),
                taxable_base=sum(
                    (
                        Decimal(
                            event
                            .recognized_taxable_base
                        )
                        for event
                        in source_events
                    ),
                    ZERO,
                ),
                tax_amount=sum(
                    (
                        Decimal(
                            event
                            .recognized_tax_amount
                        )
                        for event
                        in source_events
                    ),
                    ZERO,
                ),
            )
        )

    return tuple(
        sorted(
            targets,
            key=_source_sort_key,
        )
    )


async def _lock_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> TaxCalculation:
    calculation = (
        await db.execute(
            select(
                TaxCalculation
            )
            .where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.id
                == tax_calculation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise (
            TaxRecognitionCalculationNotFoundError(
                "TaxCalculation not found"
            )
        )

    return calculation


async def _load_invoice_line_quantity(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> Decimal:
    quantity = (
        await db.execute(
            select(
                TradeDocumentLine.quantity
            )
            .where(
                TradeDocumentLine.company_id
                == calculation.company_id,
                TradeDocumentLine.trade_document_id
                == calculation.trade_document_id,
                TradeDocumentLine.id
                == calculation.trade_document_line_id,
            )
        )
    ).scalar_one_or_none()

    if quantity is None:
        raise (
            TaxRecognitionDataIntegrityError(
                "TaxCalculation invoice line "
                "does not exist"
            )
        )

    quantity = Decimal(
        quantity
    )

    if quantity <= ZERO:
        raise (
            TaxRecognitionDataIntegrityError(
                "TaxCalculation invoice line "
                "quantity must be positive"
            )
        )

    return quantity


async def _load_fulfillment_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    invoice_line_quantity = (
        await _load_invoice_line_quantity(
            db,
            calculation=calculation,
        )
    )

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                Document,
            )
            .join(
                TradeFulfillment,
                and_(
                    TradeFulfillment.company_id
                    == (
                        InvoiceFulfillmentAllocation
                        .company_id
                    ),
                    TradeFulfillment.id
                    == (
                        InvoiceFulfillmentAllocation
                        .fulfillment_id
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    Document.company_id
                    == TradeFulfillment.company_id,
                    Document.id
                    == (
                        TradeFulfillment
                        .warehouse_document_id
                    ),
                ),
            )
            .where(
                (
                    InvoiceFulfillmentAllocation
                    .company_id
                    == calculation.company_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_id
                    == calculation.trade_document_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_line_id
                    == (
                        calculation
                        .trade_document_line_id
                    )
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
        )
    ).all()

    candidates = []

    for allocation, document in rows:
        if (
            document.status
            != DocumentStatus.POSTED
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "ACTIVE invoice fulfillment "
                    "allocation must reference a "
                    "POSTED warehouse document"
                )
            )

        if (
            document.document_type
            != DocumentType.ISSUE
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "OUTPUT VAT fulfillment source "
                    "must reference warehouse ISSUE"
                )
            )

        candidate = (
            build_fulfillment_recognition_candidate(
                calculation=calculation,
                source_id=allocation.id,
                event_date=(
                    document.document_date
                ),
                allocation_quantity=Decimal(
                    allocation.quantity
                ),
                invoice_line_quantity=(
                    invoice_line_quantity
                ),
            )
        )

        candidates.append(
            candidate
        )

    return tuple(
        candidates
    )


async def _load_invoice_open_item(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> CounterpartyOpenItem:
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            )
            .where(
                CounterpartyOpenItem.company_id
                == calculation.company_id,
                CounterpartyOpenItem.trade_document_id
                == calculation.trade_document_id,
            )
        )
    ).scalar_one_or_none()

    if item is None:
        raise (
            TaxRecognitionDataIntegrityError(
                "TaxCalculation invoice has no "
                "CounterpartyOpenItem"
            )
        )

    if (
        item.item_type
        != CounterpartyOpenItemType.RECEIVABLE
    ):
        raise (
            TaxRecognitionDataIntegrityError(
                "OUTPUT VAT settlement source "
                "requires RECEIVABLE open item"
            )
        )

    if (
        item.currency_code
        != calculation.currency_code
    ):
        raise (
            TaxRecognitionDataIntegrityError(
                "TaxCalculation and open item "
                "currency mismatch"
            )
        )

    if (
        Decimal(
            item.original_amount
        )
        <= ZERO
    ):
        raise (
            TaxRecognitionDataIntegrityError(
                "Invoice open item amount "
                "must be positive"
            )
        )

    return item


async def _load_settlement_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    open_item = (
        await _load_invoice_open_item(
            db,
            calculation=calculation,
        )
    )

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
                    == calculation.company_id
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

    for allocation, payment in rows:
        if (
            payment.status
            != PaymentStatus.CONFIRMED
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "ACTIVE settlement allocation "
                    "must reference CONFIRMED payment"
                )
            )

        if (
            payment.direction
            != PaymentDirection.INCOMING
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "OUTPUT VAT settlement source "
                    "must use INCOMING payment"
                )
            )

        if (
            payment.currency_code
            != calculation.currency_code
        ):
            raise (
                TaxRecognitionDataIntegrityError(
                    "TaxCalculation and payment "
                    "currency mismatch"
                )
            )

        candidate = (
            build_settlement_recognition_candidate(
                calculation=calculation,
                source_id=allocation.id,
                event_date=(
                    payment.payment_date
                ),
                settlement_amount=Decimal(
                    allocation.amount
                ),
                invoice_total_amount=Decimal(
                    open_item.original_amount
                ),
            )
        )

        candidates.append(
            candidate
        )

    return tuple(
        candidates
    )


async def load_active_output_tax_recognition_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise (
            TaxRecognitionDataIntegrityError(
                "Unsupported TaxCalculation "
                "recognition method"
            )
        ) from exc

    candidates = []

    if (
        method
        == TaxRecognitionMethod.FIRST_EVENT
    ):
        candidates.extend(
            await _load_fulfillment_candidates(
                db,
                calculation=calculation,
            )
        )

    candidates.extend(
        await _load_settlement_candidates(
            db,
            calculation=calculation,
        )
    )

    return tuple(
        candidates
    )


async def _load_tax_recognition_events(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    TaxRecognitionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TaxRecognitionEvent
                )
                .where(
                    TaxRecognitionEvent.company_id
                    == company_id,
                    TaxRecognitionEvent.tax_calculation_id
                    == tax_calculation_id,
                )
                .order_by(
                    TaxRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def reconcile_output_tax_calculation_from_active_sources(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> OutputTaxRecognitionReconciliationResult:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if tax_calculation_id <= 0:
        raise ValueError(
            "tax_calculation_id must be "
            "greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    calculation = (
        await _lock_tax_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    build_output_tax_recognition_targets(
        calculation=calculation,
        candidates=(),
    )

    candidates = (
        await load_active_output_tax_recognition_candidates(
            db,
            calculation=calculation,
        )
    )

    desired_targets = (
        build_output_tax_recognition_targets(
            calculation=calculation,
            candidates=candidates,
        )
    )

    events = (
        await _load_tax_recognition_events(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    current_targets = (
        build_current_output_tax_recognition_targets(
            events
        )
    )

    adjustments = (
        order_output_tax_reconciliations(
            current_targets=current_targets,
            desired_targets=desired_targets,
        )
    )

    created_events = []

    for target in adjustments:
        source_kwargs = {}

        if (
            target.kind
            == TaxRecognitionCandidateKind
            .FULFILLMENT
        ):
            source_kwargs[
                "invoice_fulfillment_allocation_id"
            ] = target.source_id

        elif (
            target.kind
            == TaxRecognitionCandidateKind
            .SETTLEMENT
        ):
            source_kwargs[
                "payment_settlement_allocation_id"
            ] = target.source_id

        else:
            raise (
                TaxRecognitionDataIntegrityError(
                    "Unsupported recognition "
                    "source kind"
                )
            )

        created = (
            await reconcile_output_tax_recognition_source(
                db,
                company_id=company_id,
                tax_calculation_id=(
                    tax_calculation_id
                ),
                recognition_date=(
                    target.event_date
                ),
                target_taxable_base=(
                    target.taxable_base
                ),
                target_tax_amount=(
                    target.tax_amount
                ),
                created_by=created_by,
                reversal_date=(
                    adjustment_date
                ),
                **source_kwargs,
            )
        )

        created_events.extend(
            created
        )

    return (
        OutputTaxRecognitionReconciliationResult(
            tax_calculation_id=(
                tax_calculation_id
            ),
            candidates=candidates,
            current_targets=(
                current_targets
            ),
            desired_targets=(
                desired_targets
            ),
            adjustments=adjustments,
            created_events=tuple(
                created_events
            ),
        )
    )
