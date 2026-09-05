from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
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
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.purchase_return_vat_adjustment_calculation_service import (
    PurchaseReturnVatAdjustmentTarget,
    build_purchase_return_vat_adjustment_target,
)
from app.services.purchase_return_vat_adjustment_persistence_service import (
    PurchaseReturnVatAdjustmentPersistenceError,
    reconcile_purchase_return_vat_adjustment_source,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


ZERO = Decimal("0")


class PurchaseReturnVatAdjustmentReconciliationError(
    Exception
):
    """Base Purchase Return VAT adjustment reconciliation error."""


class PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
    PurchaseReturnVatAdjustmentReconciliationError
):
    """Persistent Purchase Return VAT source state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAllocationCandidate:
    """
    One ACTIVE InvoiceFulfillmentAllocation in deterministic
    commercial/VAT allocation order.
    """

    source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatRecognitionCandidate:
    """
    One ACTIVE PurchaseReturnRecognitionEvent consuming VAT capacity
    from a single InvoiceFulfillmentAllocation.

    returned_tax_amount is an immutable cross-check only.
    It does not drive the independent VAT calculation.
    """

    source_id: int
    event_date: date
    quantity: Decimal
    returned_tax_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAmountSlice:
    taxable_base: Decimal
    tax_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class _RequestedPrreState:
    source_event: PurchaseReturnRecognitionEvent
    is_active: bool
    active_events: tuple[
        PurchaseReturnRecognitionEvent,
        ...,
    ]


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAdjustmentReconciliationResult:
    requested_event_id: int
    source_prre_id: int
    source_is_active: bool
    desired_target: PurchaseReturnVatAdjustmentTarget | None
    current_source_keys: tuple[
        tuple[
            int,
            str,
        ],
        ...,
    ]
    created_events: tuple[
        PurchaseReturnVatAdjustmentEvent,
        ...,
    ]


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = (
            value
            if isinstance(
                value,
                Decimal,
            )
            else Decimal(
                str(
                    value
                )
            )
        )
    except Exception as exc:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _positive_id(
    value,
    *,
    field: str,
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
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _currency(
    value,
) -> str:
    normalized = str(
        value
    ).strip().upper()

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "currency_code must contain exactly "
                "three alphabetic characters"
            )
        )

    return normalized


def _business_date(
    value,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _cumulative_amount(
    *,
    total_amount: Decimal,
    total_quantity: Decimal,
    cumulative_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    """
    Independent cumulative-delta monetary allocator.

    Used independently for:
        TaxCalculation.taxable_base
        TaxCalculation.tax_amount

    It never derives taxable base from commercial gross/tax snapshots.
    """

    total_amount = _decimal(
        total_amount,
        field="total_amount",
    )
    total_quantity = _decimal(
        total_quantity,
        field="total_quantity",
    )
    cumulative_quantity = _decimal(
        cumulative_quantity,
        field="cumulative_quantity",
    )
    currency_code = _currency(
        currency_code
    )

    if total_amount < ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "total_amount cannot be negative"
            )
        )

    if total_quantity <= ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "total_quantity must be positive"
            )
        )

    if (
        cumulative_quantity < ZERO
        or cumulative_quantity
        > total_quantity
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "cumulative quantity is outside capacity"
            )
        )

    if cumulative_quantity == ZERO:
        return round_currency_amount(
            amount=ZERO,
            currency_code=currency_code,
        )

    if cumulative_quantity == total_quantity:
        return round_currency_amount(
            amount=total_amount,
            currency_code=currency_code,
        )

    return round_currency_amount(
        amount=(
            total_amount
            * cumulative_quantity
            / total_quantity
        ),
        currency_code=currency_code,
    )


def build_invoice_vat_capacity_slices(
    *,
    invoice_line_quantity: Decimal,
    taxable_base: Decimal,
    tax_amount: Decimal,
    currency_code: str,
    candidates: Iterable[
        PurchaseReturnVatAllocationCandidate
    ],
) -> dict[
    int,
    PurchaseReturnVatAmountSlice,
]:
    """
    Allocate immutable TaxCalculation totals across ALL ACTIVE IFAs.

    Ordering:
        receipt/document date
        InvoiceFulfillmentAllocation.id

    taxable_base and tax_amount are sliced independently.
    """

    invoice_quantity = _decimal(
        invoice_line_quantity,
        field="invoice_line_quantity",
    )

    if invoice_quantity <= ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "invoice_line_quantity must be positive"
            )
        )

    base_total = _decimal(
        taxable_base,
        field="TaxCalculation taxable_base",
    )

    tax_total = _decimal(
        tax_amount,
        field="TaxCalculation tax_amount",
    )

    if base_total < ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "TaxCalculation taxable_base cannot be negative"
            )
        )

    if tax_total < ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "TaxCalculation tax_amount cannot be negative"
            )
        )

    currency_code = _currency(
        currency_code
    )

    normalized = []
    seen = set()

    for raw in tuple(
        candidates
    ):
        if not isinstance(
            raw,
            PurchaseReturnVatAllocationCandidate,
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT allocation candidate has invalid type"
                )
            )

        source_id = _positive_id(
            raw.source_id,
            field="VAT allocation source_id",
        )

        if source_id in seen:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Duplicate VAT allocation source"
                )
            )

        seen.add(
            source_id
        )

        event_date = _business_date(
            raw.event_date,
            field="VAT allocation event_date",
        )

        quantity = _decimal(
            raw.quantity,
            field="VAT allocation quantity",
        )

        if quantity <= ZERO:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT allocation quantity must be positive"
                )
            )

        normalized.append(
            PurchaseReturnVatAllocationCandidate(
                source_id=source_id,
                event_date=event_date,
                quantity=quantity,
            )
        )

    normalized.sort(
        key=lambda candidate: (
            candidate.event_date,
            candidate.source_id,
        )
    )

    result = {}
    allocated_before = ZERO
    base_before = ZERO
    tax_before = ZERO

    for candidate in normalized:
        allocated_after = (
            allocated_before
            + candidate.quantity
        )

        base_after = _cumulative_amount(
            total_amount=base_total,
            total_quantity=invoice_quantity,
            cumulative_quantity=allocated_after,
            currency_code=currency_code,
        )

        tax_after = _cumulative_amount(
            total_amount=tax_total,
            total_quantity=invoice_quantity,
            cumulative_quantity=allocated_after,
            currency_code=currency_code,
        )

        base_slice = (
            base_after
            - base_before
        )

        tax_slice = (
            tax_after
            - tax_before
        )

        if (
            base_slice < ZERO
            or tax_slice < ZERO
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT allocation produced negative slice"
                )
            )

        result[
            candidate.source_id
        ] = PurchaseReturnVatAmountSlice(
            taxable_base=base_slice,
            tax_amount=tax_slice,
        )

        allocated_before = (
            allocated_after
        )

        base_before = (
            base_after
        )

        tax_before = (
            tax_after
        )

    return result


def build_purchase_return_vat_slices(
    *,
    allocation_quantity: Decimal,
    allocation_taxable_base: Decimal,
    allocation_tax_amount: Decimal,
    currency_code: str,
    candidates: Iterable[
        PurchaseReturnVatRecognitionCandidate
    ],
) -> dict[
    int,
    PurchaseReturnVatAmountSlice,
]:
    """
    Allocate one IFA VAT capacity across ACTIVE PRRE sources.

    Ordering:
        PRRE recognition_date
        PRRE.id

    The calculated tax slice must exactly agree with the immutable
    PRRE returned_tax_amount snapshot. Mismatch fails closed.
    """

    allocation_quantity = _decimal(
        allocation_quantity,
        field="allocation_quantity",
    )

    if allocation_quantity <= ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "allocation_quantity must be positive"
            )
        )

    base_capacity = _decimal(
        allocation_taxable_base,
        field="allocation_taxable_base",
    )

    tax_capacity = _decimal(
        allocation_tax_amount,
        field="allocation_tax_amount",
    )

    if (
        base_capacity < ZERO
        or tax_capacity < ZERO
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "allocation VAT capacities cannot be negative"
            )
        )

    currency_code = _currency(
        currency_code
    )

    normalized = []
    seen = set()

    for raw in tuple(
        candidates
    ):
        if not isinstance(
            raw,
            PurchaseReturnVatRecognitionCandidate,
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "PRRE VAT candidate has invalid type"
                )
            )

        source_id = _positive_id(
            raw.source_id,
            field="PRRE VAT source_id",
        )

        if source_id in seen:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Duplicate PRRE VAT source"
                )
            )

        seen.add(
            source_id
        )

        event_date = _business_date(
            raw.event_date,
            field="PRRE VAT event_date",
        )

        quantity = _decimal(
            raw.quantity,
            field="PRRE returned quantity",
        )

        expected_tax = round_currency_amount(
            amount=_decimal(
                raw.returned_tax_amount,
                field="PRRE returned_tax_amount",
            ),
            currency_code=currency_code,
        )

        if (
            quantity <= ZERO
            or expected_tax < ZERO
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "PRRE VAT source contains invalid quantity/tax"
                )
            )

        normalized.append(
            (
                source_id,
                event_date,
                quantity,
                expected_tax,
            )
        )

    normalized.sort(
        key=lambda item: (
            item[1],
            item[0],
        )
    )

    result = {}
    returned_before = ZERO
    base_before = ZERO
    tax_before = ZERO

    for (
        source_id,
        _event_date,
        quantity,
        expected_tax,
    ) in normalized:
        returned_after = (
            returned_before
            + quantity
        )

        base_after = _cumulative_amount(
            total_amount=base_capacity,
            total_quantity=allocation_quantity,
            cumulative_quantity=returned_after,
            currency_code=currency_code,
        )

        tax_after = _cumulative_amount(
            total_amount=tax_capacity,
            total_quantity=allocation_quantity,
            cumulative_quantity=returned_after,
            currency_code=currency_code,
        )

        base_slice = (
            base_after
            - base_before
        )

        tax_slice = (
            tax_after
            - tax_before
        )

        if tax_slice != expected_tax:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Calculated Purchase Return VAT tax slice "
                    "does not match immutable "
                    "PurchaseReturnRecognitionEvent."
                    "returned_tax_amount"
                )
            )

        result[
            source_id
        ] = PurchaseReturnVatAmountSlice(
            taxable_base=base_slice,
            tax_amount=tax_slice,
        )

        returned_before = (
            returned_after
        )

        base_before = (
            base_after
        )

        tax_before = (
            tax_after
        )

    return result


def _active_original_rows(
    rows: Iterable,
    *,
    label: str,
):
    rows = tuple(
        rows
    )

    by_id = {}
    originals = []
    reversed_ids = set()

    for row in rows:
        row_id = _positive_id(
            row.id,
            field=f"{label} id",
        )

        if row_id in by_id:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    f"Duplicate {label} id"
                )
            )

        by_id[
            row_id
        ] = row

        if row.reversal_of_id is None:
            originals.append(
                row
            )

    for row in rows:
        if row.reversal_of_id is None:
            continue

        reversal_of_id = _positive_id(
            row.reversal_of_id,
            field=f"{label} reversal_of_id",
        )

        original = by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    f"{label} reversal references "
                    "row outside history"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    f"{label} reversal references "
                    "a non-original row"
                )
            )

        if reversal_of_id in reversed_ids:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    f"{label} original has multiple reversals"
                )
            )

        reversed_ids.add(
            reversal_of_id
        )

    return tuple(
        row
        for row in originals
        if row.id
        not in reversed_ids
    )


async def _load_requested_prre_state(
    db: AsyncSession,
    *,
    company_id: int,
    requested_event_id: int,
) -> _RequestedPrreState:
    requested = (
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
                    .id
                    == requested_event_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if requested is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Return recognition event not found"
            )
        )

    allocation_id = _positive_id(
        requested.invoice_fulfillment_allocation_id,
        field="invoice_fulfillment_allocation_id",
    )

    history = tuple(
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
                        == allocation_id
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

    by_id = {
        row.id: row
        for row in history
    }

    source_id = (
        requested.reversal_of_id
        if requested.reversal_of_id
        is not None
        else requested.id
    )

    source_id = _positive_id(
        source_id,
        field="source PRRE id",
    )

    source = by_id.get(
        source_id
    )

    if source is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Requested PRRE source is outside locked history"
            )
        )

    active = _active_original_rows(
        history,
        label="PurchaseReturnRecognitionEvent",
    )

    active_ids = {
        row.id
        for row in active
    }

    return _RequestedPrreState(
        source_event=source,
        is_active=(
            source.id
            in active_ids
        ),
        active_events=active,
    )


async def _load_active_source_target(
    db: AsyncSession,
    *,
    company_id: int,
    state: _RequestedPrreState,
    adjustment_date: date,
    basis_kind: str,
) -> PurchaseReturnVatAdjustmentTarget:
    source = state.source_event

    allocation_id = _positive_id(
        source.invoice_fulfillment_allocation_id,
        field="invoice_fulfillment_allocation_id",
    )

    allocation = (
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
                    .id
                    == allocation_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "PRRE InvoiceFulfillmentAllocation not found"
            )
        )

    if (
        allocation.status
        != InvoiceFulfillmentAllocationStatus.ACTIVE
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Active PRRE requires ACTIVE "
                "InvoiceFulfillmentAllocation"
            )
        )

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
                    == allocation.invoice_id
                ),
                (
                    TradeDocumentLine.id
                    == allocation.invoice_line_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Invoice line not found"
            )
        )

    document = (
        await db.execute(
            select(
                TradeDocument
            ).where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == allocation.invoice_id,
            )
        )
    ).scalar_one_or_none()

    if document is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Invoice not found"
            )
        )

    if (
        document.direction
        != TradeDirection.PURCHASE
        or document.kind
        != TradeDocumentKind.INVOICE
        or document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "VAT adjustment requires confirmed "
                "Purchase Invoice"
            )
        )

    if (
        line.company_id
        != company_id
        or line.trade_document_id
        != document.id
        or line.product_id
        != allocation.product_id
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Invoice line source mismatch"
            )
        )

    if line.tax_rate_code is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Return VAT adjustment requires "
                "VAT-configured invoice line"
            )
        )

    calculation = (
        await db.execute(
            select(
                TaxCalculation
            ).where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.trade_document_id
                == allocation.invoice_id,
                TaxCalculation.trade_document_line_id
                == allocation.invoice_line_id,
                TaxCalculation.tax_type
                == TaxType.VAT,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Immutable INPUT VAT TaxCalculation not found"
            )
        )

    if (
        calculation.direction
        != TaxDirection.INPUT
        or calculation.product_id
        != line.product_id
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "TaxCalculation is not matching INPUT VAT source"
            )
        )

    currency_code = _currency(
        document.currency_code
    )

    if (
        _currency(
            calculation.currency_code
        )
        != currency_code
        or _currency(
            source.currency_code
        )
        != currency_code
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Return VAT currency mismatch"
            )
        )

    invoice_quantity = _decimal(
        line.quantity,
        field="invoice line quantity",
    )

    if invoice_quantity <= ZERO:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Purchase Invoice line quantity must be positive"
            )
        )

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                Document,
            )
            .join(
                TradeFulfillmentLine,
                and_(
                    (
                        TradeFulfillmentLine.company_id
                        == InvoiceFulfillmentAllocation.company_id
                    ),
                    (
                        TradeFulfillmentLine.fulfillment_id
                        == InvoiceFulfillmentAllocation.fulfillment_id
                    ),
                    (
                        TradeFulfillmentLine.id
                        == InvoiceFulfillmentAllocation.fulfillment_line_id
                    ),
                    (
                        TradeFulfillmentLine.trade_document_id
                        == InvoiceFulfillmentAllocation.order_id
                    ),
                    (
                        TradeFulfillmentLine.trade_document_line_id
                        == InvoiceFulfillmentAllocation.order_line_id
                    ),
                    (
                        TradeFulfillmentLine.product_id
                        == InvoiceFulfillmentAllocation.product_id
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    (
                        Document.company_id
                        == InvoiceFulfillmentAllocation.company_id
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
                    == allocation.invoice_id
                ),
                (
                    InvoiceFulfillmentAllocation.invoice_line_id
                    == allocation.invoice_line_id
                ),
                (
                    InvoiceFulfillmentAllocation.status
                    == InvoiceFulfillmentAllocationStatus.ACTIVE
                ),
            )
            .order_by(
                Document.document_date,
                InvoiceFulfillmentAllocation.id,
            )
            .with_for_update()
        )
    ).all()

    allocation_candidates = []
    allocation_by_id = {}

    for (
        peer,
        receipt_document,
    ) in rows:
        if (
            receipt_document.status
            != DocumentStatus.POSTED
            or receipt_document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "ACTIVE Purchase allocation must "
                    "reference POSTED RECEIPT"
                )
            )

        if peer.product_id != line.product_id:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Purchase allocation product mismatch"
                )
            )

        peer_quantity = _decimal(
            peer.quantity,
            field="InvoiceFulfillmentAllocation quantity",
        )

        if peer_quantity <= ZERO:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "ACTIVE allocation quantity must be positive"
                )
            )

        peer_id = _positive_id(
            peer.id,
            field="InvoiceFulfillmentAllocation id",
        )

        if peer_id in allocation_by_id:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Duplicate ACTIVE InvoiceFulfillmentAllocation"
                )
            )

        allocation_by_id[
            peer_id
        ] = peer

        allocation_candidates.append(
            PurchaseReturnVatAllocationCandidate(
                source_id=peer_id,
                event_date=_business_date(
                    receipt_document.document_date,
                    field="receipt document date",
                ),
                quantity=peer_quantity,
            )
        )

    if allocation_id not in allocation_by_id:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "PRRE allocation is no longer in ACTIVE peer set"
            )
        )

    capacity_by_allocation = (
        build_invoice_vat_capacity_slices(
            invoice_line_quantity=invoice_quantity,
            taxable_base=_decimal(
                calculation.taxable_base,
                field="TaxCalculation taxable_base",
            ),
            tax_amount=_decimal(
                calculation.tax_amount,
                field="TaxCalculation tax_amount",
            ),
            currency_code=currency_code,
            candidates=allocation_candidates,
        )
    )

    allocation_capacity = (
        capacity_by_allocation.get(
            allocation_id
        )
    )

    if allocation_capacity is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "VAT capacity for PRRE allocation is missing"
            )
        )

    active_prre_candidates = []

    for event in state.active_events:
        if (
            event.invoice_fulfillment_allocation_id
            != allocation_id
        ):
            continue

        if _currency(
            event.currency_code
        ) != currency_code:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Active PRRE currency mismatch"
                )
            )

        active_prre_candidates.append(
            PurchaseReturnVatRecognitionCandidate(
                source_id=_positive_id(
                    event.id,
                    field="PRRE id",
                ),
                event_date=_business_date(
                    event.recognition_date,
                    field="PRRE recognition_date",
                ),
                quantity=_decimal(
                    event.returned_quantity,
                    field="PRRE returned_quantity",
                ),
                returned_tax_amount=_decimal(
                    event.returned_tax_amount,
                    field="PRRE returned_tax_amount",
                ),
            )
        )

    vat_slices = build_purchase_return_vat_slices(
        allocation_quantity=_decimal(
            allocation.quantity,
            field="target allocation quantity",
        ),
        allocation_taxable_base=(
            allocation_capacity.taxable_base
        ),
        allocation_tax_amount=(
            allocation_capacity.tax_amount
        ),
        currency_code=currency_code,
        candidates=active_prre_candidates,
    )

    source_slice = vat_slices.get(
        source.id
    )

    if source_slice is None:
        raise (
            PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                "Active PRRE has no calculated VAT slice"
            )
        )

    return build_purchase_return_vat_adjustment_target(
        purchase_return_recognition_event_id=(
            _positive_id(
                source.id,
                field="source PRRE id",
            )
        ),
        tax_calculation_id=_positive_id(
            calculation.id,
            field="TaxCalculation id",
        ),
        adjustment_date=adjustment_date,
        basis_kind=basis_kind,
        adjusted_taxable_base=(
            source_slice.taxable_base
        ),
        adjusted_tax_amount=(
            source_slice.tax_amount
        ),
        currency_code=currency_code,
    )


async def _load_vat_history(
    db: AsyncSession,
    *,
    company_id: int,
    source_prre_id: int,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnVatAdjustmentEvent
                )
                .where(
                    (
                        PurchaseReturnVatAdjustmentEvent
                        .company_id
                        == company_id
                    ),
                    (
                        PurchaseReturnVatAdjustmentEvent
                        .purchase_return_recognition_event_id
                        == source_prre_id
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


def _active_vat_sources(
    history: Iterable[
        PurchaseReturnVatAdjustmentEvent
    ],
) -> dict[
    tuple[
        int,
        str,
    ],
    PurchaseReturnVatAdjustmentEvent,
]:
    active = _active_original_rows(
        history,
        label="PurchaseReturnVatAdjustmentEvent",
    )

    result = {}

    for event in active:
        key = (
            _positive_id(
                event.tax_calculation_id,
                field="VAT history tax_calculation_id",
            ),
            str(
                event.basis_kind
            ),
        )

        if key in result:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT adjustment source key has "
                    "multiple active originals"
                )
            )

        result[
            key
        ] = event

    return result


def _same_active_peer_vat_state(
    *,
    current: PurchaseReturnVatAdjustmentEvent,
    target: PurchaseReturnVatAdjustmentTarget,
) -> bool:
    """
    Compare economic VAT state for automatic peer reconciliation.

    adjustment_date is deliberately excluded.

    A later reconciliation date alone is not an economic state
    change and must not create reversal/replacement churn for an
    otherwise unchanged peer.
    """

    return (
        _positive_id(
            current.purchase_return_recognition_event_id,
            field=(
                "peer VAT "
                "purchase_return_recognition_event_id"
            ),
        )
        == target.purchase_return_recognition_event_id
        and _positive_id(
            current.tax_calculation_id,
            field="peer VAT tax_calculation_id",
        )
        == target.tax_calculation_id
        and str(
            current.basis_kind
        )
        == target.basis_kind
        and _decimal(
            current.adjusted_taxable_base,
            field="peer VAT adjusted_taxable_base",
        )
        == target.adjusted_taxable_base
        and _decimal(
            current.adjusted_tax_amount,
            field="peer VAT adjusted_tax_amount",
        )
        == target.adjusted_tax_amount
        and _currency(
            current.currency_code
        )
        == target.currency_code
    )


async def _reconcile_active_vat_peer_sources(
    db: AsyncSession,
    *,
    company_id: int,
    state: _RequestedPrreState,
    requested_source_prre_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    """
    Re-evaluate existing ACTIVE economic VAT sources for other
    ACTIVE PRRE peers of the same InvoiceFulfillmentAllocation.

    Purchase Return VAT slices inside one IFA are cumulative-delta
    allocations across the complete ACTIVE PRRE set. Therefore,
    removing/replacing an earlier PRRE may shift a rounding penny
    or taxable-base slice belonging to a later peer.

    Scope is intentionally narrow:

    - requested PRRE reconciliation remains owned by the public
      one-source reconciler;
    - only other ACTIVE PRRE peers are considered here;
    - a peer without an existing ACTIVE VAT source is not created
      automatically;
    - the peer's own current basis_kind is preserved;
    - an unchanged peer amount state is a no-op even when the
      caller supplied a later adjustment_date;
    - no JournalEntry, legal INPUT-credit correction, supplier
      clearing, COMMIT, or ROLLBACK occurs here.
    """

    requested_source_prre_id = _positive_id(
        requested_source_prre_id,
        field="requested_source_prre_id",
    )

    peers = tuple(
        sorted(
            (
                event
                for event in state.active_events
                if (
                    _positive_id(
                        event.id,
                        field="active peer PRRE id",
                    )
                    != requested_source_prre_id
                )
            ),
            key=lambda event: (
                _business_date(
                    event.recognition_date,
                    field="active peer recognition_date",
                ),
                _positive_id(
                    event.id,
                    field="active peer PRRE id",
                ),
            ),
        )
    )

    created_events = []

    for peer in peers:
        peer_id = _positive_id(
            peer.id,
            field="active peer PRRE id",
        )

        history = await _load_vat_history(
            db,
            company_id=company_id,
            source_prre_id=peer_id,
        )

        current_by_key = _active_vat_sources(
            history
        )

        if not current_by_key:
            continue

        if len(
            current_by_key
        ) != 1:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Active PRRE peer VAT history has "
                    "multiple active source keys"
                )
            )

        (
            current_key,
            current,
        ) = next(
            iter(
                current_by_key.items()
            )
        )

        if (
            _positive_id(
                current
                .purchase_return_recognition_event_id,
                field=(
                    "active peer VAT "
                    "purchase_return_recognition_event_id"
                ),
            )
            != peer_id
        ):
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Active PRRE peer VAT source identity mismatch"
                )
            )

        peer_state = _RequestedPrreState(
            source_event=peer,
            is_active=True,
            active_events=state.active_events,
        )

        desired_target = (
            await _load_active_source_target(
                db,
                company_id=company_id,
                state=peer_state,
                adjustment_date=adjustment_date,
                basis_kind=current_key[1],
            )
        )

        desired_key = (
            desired_target.tax_calculation_id,
            desired_target.basis_kind,
        )

        if desired_key != current_key:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "Active PRRE peer VAT target changed "
                    "TaxCalculation or basis_kind"
                )
            )

        if _same_active_peer_vat_state(
            current=current,
            target=desired_target,
        ):
            continue

        try:
            created = (
                await reconcile_purchase_return_vat_adjustment_source(
                    db,
                    company_id=company_id,
                    target=desired_target,
                    created_by=created_by,
                    reversal_date=adjustment_date,
                )
            )

        except PurchaseReturnVatAdjustmentPersistenceError as exc:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT adjustment immutable persistence "
                    f"failed for active peer PRRE {peer_id}: {exc}"
                )
            ) from exc

        created_events.extend(
            created
        )

    return tuple(
        created_events
    )


async def reconcile_purchase_return_vat_adjustment_for_recognition_event(
    db: AsyncSession,
    *,
    company_id: int,
    purchase_return_recognition_event_id: int,
    adjustment_date: date,
    basis_kind: str,
    created_by: int,
) -> PurchaseReturnVatAdjustmentReconciliationResult:
    """
    Reconcile VAT-adjustment state for one PRRE source.

    Requested PRRE may be:
        active original
        reversal row
        historical reversed original

    Active PRRE:
        recompute independent VAT base/tax from TaxCalculation
        and ACTIVE IFA / PRRE chronology.

    Inactive PRRE:
        all currently-active VAT-adjustment source keys for that
        PRRE are targeted to zero.

    basis_kind changes:
        old active basis source is reversed first
        new desired basis source is then created.

    Still deliberately outside this service:
        JournalEntry
        641 / 644
        InputVatFulfillmentBridge
        TaxRecognitionEvent
        TaxCreditEvidence
        supplier clearing

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    requested_event_id = _positive_id(
        purchase_return_recognition_event_id,
        field="purchase_return_recognition_event_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    adjustment_date = _business_date(
        adjustment_date,
        field="adjustment_date",
    )

    state = await _load_requested_prre_state(
        db,
        company_id=company_id,
        requested_event_id=requested_event_id,
    )

    source_prre_id = _positive_id(
        state.source_event.id,
        field="source PRRE id",
    )

    history = await _load_vat_history(
        db,
        company_id=company_id,
        source_prre_id=source_prre_id,
    )

    current_by_key = _active_vat_sources(
        history
    )

    desired_target = None

    if state.is_active:
        desired_target = (
            await _load_active_source_target(
                db,
                company_id=company_id,
                state=state,
                adjustment_date=adjustment_date,
                basis_kind=basis_kind,
            )
        )

        desired_key = (
            desired_target.tax_calculation_id,
            desired_target.basis_kind,
        )

        for (
            tax_calculation_id,
            _basis,
        ) in current_by_key:
            if (
                tax_calculation_id
                != desired_target.tax_calculation_id
            ):
                raise (
                    PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                        "Active PRRE VAT history references "
                        "unexpected TaxCalculation"
                    )
                )
    else:
        desired_key = None

    created_events = []

    for key in sorted(
        current_by_key
    ):
        if (
            desired_key is not None
            and key == desired_key
        ):
            continue

        current = current_by_key[
            key
        ]

        zero_target = (
            build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=(
                    source_prre_id
                ),
                tax_calculation_id=key[0],
                adjustment_date=adjustment_date,
                basis_kind=key[1],
                adjusted_taxable_base=ZERO,
                adjusted_tax_amount=ZERO,
                currency_code=_currency(
                    current.currency_code
                ),
            )
        )

        try:
            created = (
                await reconcile_purchase_return_vat_adjustment_source(
                    db,
                    company_id=company_id,
                    target=zero_target,
                    created_by=created_by,
                    reversal_date=adjustment_date,
                )
            )
        except PurchaseReturnVatAdjustmentPersistenceError as exc:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT adjustment immutable persistence "
                    f"failed for old source {key}: {exc}"
                )
            ) from exc

        created_events.extend(
            created
        )

    if desired_target is not None:
        try:
            created = (
                await reconcile_purchase_return_vat_adjustment_source(
                    db,
                    company_id=company_id,
                    target=desired_target,
                    created_by=created_by,
                    reversal_date=adjustment_date,
                )
            )
        except PurchaseReturnVatAdjustmentPersistenceError as exc:
            raise (
                PurchaseReturnVatAdjustmentReconciliationDataIntegrityError(
                    "VAT adjustment immutable persistence "
                    f"failed for desired source: {exc}"
                )
            ) from exc

        created_events.extend(
            created
        )

    peer_created_events = (
        await _reconcile_active_vat_peer_sources(
            db,
            company_id=company_id,
            state=state,
            requested_source_prre_id=source_prre_id,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    )

    created_events.extend(
        peer_created_events
    )

    return PurchaseReturnVatAdjustmentReconciliationResult(
        requested_event_id=requested_event_id,
        source_prre_id=source_prre_id,
        source_is_active=state.is_active,
        desired_target=desired_target,
        current_source_keys=tuple(
            sorted(
                current_by_key
            )
        ),
        created_events=tuple(
            created_events
        ),
    )
