from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

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
from app.models.input_vat_fulfillment_bridge_event import (
    InputVatFulfillmentBridgeEvent,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeCandidate,
    InputVatFulfillmentBridgeDataIntegrityError,
    InputVatFulfillmentBridgeTarget,
    build_input_vat_fulfillment_bridge_targets,
)
from app.services.input_vat_fulfillment_bridge_persistence_service import (
    build_current_input_vat_fulfillment_bridge_targets,
    reconcile_input_vat_fulfillment_bridge_source,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0")


class InputVatFulfillmentBridgeReconciliationError(
    Exception
):
    """Base multi-source INPUT VAT bridge reconciliation error."""


class InputVatFulfillmentBridgeCalculationNotFoundError(
    InputVatFulfillmentBridgeReconciliationError
):
    """Required immutable TaxCalculation was not found."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputVatFulfillmentBridgeReconciliationResult:
    tax_calculation_id: int

    desired_targets: tuple[
        InputVatFulfillmentBridgeTarget,
        ...,
    ]

    reconciliation_targets: tuple[
        InputVatFulfillmentBridgeTarget,
        ...,
    ]

    created_events: tuple[
        InputVatFulfillmentBridgeEvent,
        ...,
    ]

    @property
    def created_event_ids(
        self,
    ) -> tuple[
        int,
        ...,
    ]:
        """
        Backward-compatible immutable IDs derived from created_events.

        created_events is the authoritative lifecycle payload because
        GL posting must consume events in exact persistence order.
        """

        event_ids = []

        for event in self.created_events:
            if (
                event.id is None
                or event.id <= 0
            ):
                raise (
                    InputVatFulfillmentBridgeDataIntegrityError(
                        "Created INPUT VAT fulfillment "
                        "bridge event must have a "
                        "persistent positive ID"
                    )
                )

            event_ids.append(
                int(
                    event.id
                )
            )

        return tuple(
            event_ids
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
    )


def _target_map(
    targets: Iterable[
        InputVatFulfillmentBridgeTarget
    ],
    *,
    label: str,
) -> dict[
    int,
    InputVatFulfillmentBridgeTarget,
]:
    result = {}

    for target in tuple(
        targets
    ):
        if not isinstance(
            target,
            InputVatFulfillmentBridgeTarget,
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    f"{label} target must be "
                    "InputVatFulfillmentBridgeTarget"
                )
            )

        if target.source_id in result:
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    f"Duplicate {label} INPUT VAT "
                    "bridge source_id"
                )
            )

        result[
            target.source_id
        ] = target

    return result


def _validate_same_source_provenance(
    *,
    current: InputVatFulfillmentBridgeTarget,
    desired: InputVatFulfillmentBridgeTarget,
) -> None:
    if (
        current.tax_calculation_id
        != desired.tax_calculation_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT bridge reconciliation "
                "tax_calculation_id changed "
                "for existing source"
            )
        )

    if (
        current.event_date
        != desired.event_date
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT bridge reconciliation "
                "event_date changed "
                "for existing source"
            )
        )

    if (
        current.currency_code
        != desired.currency_code
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT bridge reconciliation "
                "currency changed "
                "for existing source"
            )
        )


def build_input_vat_fulfillment_bridge_reconciliation_targets(
    *,
    desired_targets: Iterable[
        InputVatFulfillmentBridgeTarget
    ],
    current_targets: Iterable[
        InputVatFulfillmentBridgeTarget
    ],
) -> tuple[
    InputVatFulfillmentBridgeTarget,
    ...,
]:
    """
    Build ordered source reconciliation actions.

    Rules:

    1. Persistent sources absent from the ACTIVE desired set
       are reconciled to zero.

    2. Existing sources whose desired amount decreased are
       reconciled before any increase/new source.

    3. Exact matches are omitted.

    4. New zero targets are omitted because zero is represented
       by absence of an active original.

    Processing decreases/removals before increases prevents a
    rounding redistribution from temporarily overstating aggregate
    INPUT VAT bridge capacity inside the transaction.
    """

    desired_by_source = _target_map(
        desired_targets,
        label="desired",
    )

    current_by_source = _target_map(
        current_targets,
        label="current",
    )

    decreases = []
    increases = []

    for (
        source_id,
        current,
    ) in current_by_source.items():
        desired = desired_by_source.get(
            source_id
        )

        if desired is None:
            decreases.append(
                InputVatFulfillmentBridgeTarget(
                    tax_calculation_id=(
                        current
                        .tax_calculation_id
                    ),
                    source_id=(
                        current.source_id
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

        _validate_same_source_provenance(
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
        source_id,
        desired,
    ) in desired_by_source.items():
        if source_id in current_by_source:
            continue

        if desired.is_zero:
            continue

        increases.append(
            desired
        )

    decreases = sorted(
        decreases,
        key=lambda target: (
            target.event_date,
            target.source_id,
        ),
    )

    increases = sorted(
        increases,
        key=lambda target: (
            target.event_date,
            target.source_id,
        ),
    )

    return tuple(
        [
            *decreases,
            *increases,
        ]
    )


async def _load_input_tax_calculation(
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
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise (
            InputVatFulfillmentBridgeCalculationNotFoundError(
                "INPUT VAT fulfillment bridge "
                "TaxCalculation not found"
            )
        )

    if calculation.tax_type != TaxType.VAT:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "requires VAT TaxCalculation"
            )
        )

    if (
        calculation.direction
        != TaxDirection.INPUT
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "requires INPUT TaxCalculation"
            )
        )

    if (
        not isinstance(
            calculation.currency_code,
            str,
        )
        or len(
            calculation.currency_code
        ) != 3
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation "
                "currency_code must contain "
                "exactly 3 characters"
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
                (
                    TradeDocumentLine
                    .trade_document_id
                    == calculation
                    .trade_document_id
                ),
                (
                    TradeDocumentLine.id
                    == calculation
                    .trade_document_line_id
                ),
            )
        )
    ).scalar_one_or_none()

    if quantity is None:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation "
                "Purchase Invoice line "
                "does not exist"
            )
        )

    quantity = _decimal(
        quantity
    )

    if quantity <= ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Purchase Invoice line quantity "
                "must be greater than zero"
            )
        )

    return quantity


async def _load_active_fulfillment_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    InputVatFulfillmentBridgeCandidate,
    ...,
]:
    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                Document,
            )
            .join(
                TradeFulfillment,
                and_(
                    (
                        TradeFulfillment
                        .company_id
                        == (
                            InvoiceFulfillmentAllocation
                            .company_id
                        )
                    ),
                    (
                        TradeFulfillment.id
                        == (
                            InvoiceFulfillmentAllocation
                            .fulfillment_id
                        )
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    (
                        Document.company_id
                        == TradeFulfillment.company_id
                    ),
                    (
                        Document.id
                        == (
                            TradeFulfillment
                            .warehouse_document_id
                        )
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
                    == calculation
                    .trade_document_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_line_id
                    == calculation
                    .trade_document_line_id
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

    for (
        allocation,
        document,
    ) in rows:
        if (
            document.status
            != DocumentStatus.POSTED
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "ACTIVE Purchase fulfillment "
                    "allocation must reference "
                    "POSTED warehouse document"
                )
            )

        if (
            document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT fulfillment bridge "
                    "source must reference "
                    "warehouse RECEIPT"
                )
            )

        candidates.append(
            InputVatFulfillmentBridgeCandidate(
                source_id=allocation.id,
                event_date=(
                    document.document_date
                ),
                quantity=_decimal(
                    allocation.quantity
                ),
            )
        )

    return tuple(
        candidates
    )


async def _load_bridge_history(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    InputVatFulfillmentBridgeEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    InputVatFulfillmentBridgeEvent
                )
                .where(
                    (
                        InputVatFulfillmentBridgeEvent
                        .company_id
                        == calculation.company_id
                    ),
                    (
                        InputVatFulfillmentBridgeEvent
                        .tax_calculation_id
                        == calculation.id
                    ),
                )
                .order_by(
                    InputVatFulfillmentBridgeEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def reconcile_input_vat_fulfillment_bridge_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> InputVatFulfillmentBridgeReconciliationResult:
    """
    Reconcile the complete economic INPUT VAT fulfillment bridge for
    one immutable INPUT TaxCalculation.

    Desired state is derived from every ACTIVE purchase receipt
    allocation for the TaxCalculation's invoice line.

    Persistent sources no longer ACTIVE are reconciled to zero.

    Caller owns COMMIT / ROLLBACK.
    """

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

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    calculation = (
        await _load_input_tax_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    invoice_line_quantity = (
        await _load_invoice_line_quantity(
            db,
            calculation=calculation,
        )
    )

    candidates = (
        await _load_active_fulfillment_candidates(
            db,
            calculation=calculation,
        )
    )

    desired_targets = (
        build_input_vat_fulfillment_bridge_targets(
            tax_calculation_id=(
                calculation.id
            ),
            invoice_line_quantity=(
                invoice_line_quantity
            ),
            tax_amount=_decimal(
                calculation.tax_amount
            ),
            currency_code=(
                calculation.currency_code
            ),
            candidates=candidates,
        )
    )

    history = (
        await _load_bridge_history(
            db,
            calculation=calculation,
        )
    )

    current_targets = (
        build_current_input_vat_fulfillment_bridge_targets(
            events=history,
            currency_code=(
                calculation.currency_code
            ),
        )
    )

    reconciliation_targets = (
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=desired_targets,
            current_targets=current_targets,
        )
    )

    created_events = []

    for target in reconciliation_targets:
        events = (
            await reconcile_input_vat_fulfillment_bridge_source(
                db,
                company_id=company_id,
                target=target,
                currency_code=(
                    calculation.currency_code
                ),
                created_by=created_by,
                reversal_date=(
                    adjustment_date
                ),
            )
        )

        created_events.extend(
            events
        )

    return (
        InputVatFulfillmentBridgeReconciliationResult(
            tax_calculation_id=(
                calculation.id
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
