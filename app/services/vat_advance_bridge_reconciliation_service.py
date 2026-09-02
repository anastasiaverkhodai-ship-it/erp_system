from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
)
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionDataIntegrityError,
    SalesRecognitionTarget,
)
from app.services.sales_recognition_persistence_service import (
    build_current_sales_recognition_targets,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidateKind,
    TaxRecognitionSourceTarget,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionDataIntegrityError,
)
from app.services.tax_recognition_reconciliation_service import (
    build_current_output_tax_recognition_targets,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)
from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
    VatAdvanceBridgeTarget,
    build_vat_advance_bridge_target,
)
from app.services.vat_advance_bridge_persistence_service import (
    build_current_vat_advance_bridge_targets,
    reconcile_vat_advance_bridge_source,
)


ZERO = Decimal("0")


class VatAdvanceBridgeReconciliationError(Exception):
    """Base VAT advance bridge reconciliation error."""


class VatAdvanceBridgeTaxCalculationNotFoundError(
    VatAdvanceBridgeReconciliationError
):
    """Required OUTPUT VAT TaxCalculation does not exist."""


@dataclass(
    frozen=True,
    slots=True,
)
class VatAdvanceBridgeReconciliationResult:
    tax_calculation_id: int

    current_sales_targets: tuple[
        SalesRecognitionTarget,
        ...,
    ]

    current_tax_targets: tuple[
        TaxRecognitionSourceTarget,
        ...,
    ]

    current_bridge_targets: tuple[
        VatAdvanceBridgeTarget,
        ...,
    ]

    desired_targets: tuple[
        VatAdvanceBridgeTarget,
        ...,
    ]

    created_events: tuple[
        VatAdvanceBridgeEvent,
        ...,
    ]


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
    )


def _validate_context(
    *,
    tax_calculation_id: int,
    adjustment_date: date,
    currency_code: str,
) -> None:
    if tax_calculation_id <= 0:
        raise ValueError(
            "tax_calculation_id must be "
            "greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(currency_code) != 3
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge currency_code "
            "must contain exactly 3 characters"
        )


def _index_sales_targets(
    targets: Iterable[
        SalesRecognitionTarget
    ],
) -> dict[
    int,
    SalesRecognitionTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            SalesRecognitionTarget,
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Sales current "
                "state contains an invalid target"
            )

        if target.source_id <= 0:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Sales source_id "
                "must be greater than zero"
            )

        if target.source_id in result:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Sales current "
                "state contains duplicate source"
            )

        if not isinstance(
            target.event_date,
            date,
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Sales source "
                "must have an event date"
            )

        tax_amount = _decimal(
            target.tax_amount
        )

        if tax_amount < ZERO:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Sales tax amount "
                "cannot be negative"
            )

        result[
            target.source_id
        ] = target

    return result


def _index_fulfillment_tax_targets(
    targets: Iterable[
        TaxRecognitionSourceTarget
    ],
) -> dict[
    int,
    TaxRecognitionSourceTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            TaxRecognitionSourceTarget,
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Tax current "
                "state contains an invalid target"
            )

        try:
            kind = TaxRecognitionCandidateKind(
                target.kind
            )
        except ValueError as exc:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Tax current "
                "state contains unsupported source kind"
            ) from exc

        # Settlement recognition is the advance/prepayment
        # liability itself. It does NOT reduce the bridge.
        #
        # Only VAT already recognized by the same fulfillment
        # source is subtracted from Sales VAT.
        if (
            kind
            == TaxRecognitionCandidateKind
            .SETTLEMENT
        ):
            continue

        if (
            kind
            != TaxRecognitionCandidateKind
            .FULFILLMENT
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Tax current "
                "state contains unsupported source kind"
            )

        if target.source_id <= 0:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge fulfillment "
                "Tax source_id must be greater than zero"
            )

        if target.source_id in result:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge Tax current "
                "state contains duplicate fulfillment source"
            )

        if not isinstance(
            target.event_date,
            date,
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge fulfillment "
                "Tax source must have an event date"
            )

        tax_amount = _decimal(
            target.tax_amount
        )

        if tax_amount < ZERO:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge fulfillment "
                "Tax amount cannot be negative"
            )

        result[
            target.source_id
        ] = target

    return result


def _index_current_bridge_targets(
    targets: Iterable[
        VatAdvanceBridgeTarget
    ],
    *,
    tax_calculation_id: int,
    currency_code: str,
) -> dict[
    int,
    VatAdvanceBridgeTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            VatAdvanceBridgeTarget,
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge current state "
                "contains an invalid target"
            )

        if (
            target.tax_calculation_id
            != tax_calculation_id
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge current target "
                "TaxCalculation changed unexpectedly"
            )

        if (
            target.currency_code
            != currency_code
        ):
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge current target "
                "currency does not match TaxCalculation"
            )

        if target.source_id <= 0:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge current source_id "
                "must be greater than zero"
            )

        if target.source_id in result:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge current state "
                "contains duplicate source"
            )

        result[
            target.source_id
        ] = target

    return result


def build_vat_advance_bridge_reconciliation_targets(
    *,
    tax_calculation_id: int,
    sales_targets: Iterable[
        SalesRecognitionTarget
    ],
    tax_targets: Iterable[
        TaxRecognitionSourceTarget
    ],
    current_bridge_targets: Iterable[
        VatAdvanceBridgeTarget
    ],
    adjustment_date: date,
    currency_code: str,
) -> tuple[
    VatAdvanceBridgeTarget,
    ...,
]:
    """
    Build exact desired financial-accounting bridge state.

    For each InvoiceFulfillmentAllocation source:

        bridge VAT
            =
        current SalesRecognition VAT
            -
        current fulfillment-source TaxRecognition VAT

    Settlement-source VAT is intentionally ignored here.

    This is what produces:

        payment first:
            Sales VAT 20
            fulfillment VAT 0
            bridge 20

        fulfillment first:
            Sales VAT 20
            fulfillment VAT 20
            bridge 0

        partial prepayment:
            Sales VAT 20
            fulfillment VAT 10
            bridge 10

    Existing bridge sources are included even when both Sales and
    fulfillment VAT disappear, so persistence receives an explicit
    zero target and can append the immutable reversal.
    """

    _validate_context(
        tax_calculation_id=tax_calculation_id,
        adjustment_date=adjustment_date,
        currency_code=currency_code,
    )

    sales_by_source = (
        _index_sales_targets(
            sales_targets
        )
    )

    fulfillment_tax_by_source = (
        _index_fulfillment_tax_targets(
            tax_targets
        )
    )

    bridge_by_source = (
        _index_current_bridge_targets(
            current_bridge_targets,
            tax_calculation_id=(
                tax_calculation_id
            ),
            currency_code=currency_code,
        )
    )

    source_ids = (
        set(
            sales_by_source
        )
        | set(
            fulfillment_tax_by_source
        )
        | set(
            bridge_by_source
        )
    )

    targets = []

    for source_id in sorted(
        source_ids
    ):
        sales = (
            sales_by_source.get(
                source_id
            )
        )

        fulfillment_tax = (
            fulfillment_tax_by_source.get(
                source_id
            )
        )

        current_bridge = (
            bridge_by_source.get(
                source_id
            )
        )

        sales_tax_amount = (
            _decimal(
                sales.tax_amount
            )
            if sales is not None
            else ZERO
        )

        fulfillment_tax_amount = (
            _decimal(
                fulfillment_tax.tax_amount
            )
            if fulfillment_tax is not None
            else ZERO
        )

        # The bridge event belongs economically to the Sales
        # recognition date.
        #
        # If Sales recognition disappeared, preserve the current
        # bridge event_date for the zero target so immutable
        # persistence can reverse the historical source cleanly.
        if sales is not None:
            event_date = (
                sales.event_date
            )
        elif current_bridge is not None:
            event_date = (
                current_bridge.event_date
            )
        elif fulfillment_tax is not None:
            event_date = (
                fulfillment_tax.event_date
            )
        else:
            event_date = (
                adjustment_date
            )

        target = (
            build_vat_advance_bridge_target(
                tax_calculation_id=(
                    tax_calculation_id
                ),
                source_id=source_id,
                event_date=event_date,
                sales_tax_amount=(
                    sales_tax_amount
                ),
                fulfillment_tax_amount=(
                    fulfillment_tax_amount
                ),
                currency_code=(
                    currency_code
                ),
            )
        )

        targets.append(
            target
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.event_date,
                target.source_id,
            ),
        )
    )


async def _load_tax_calculation(
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
            VatAdvanceBridgeTaxCalculationNotFoundError(
                "VAT advance bridge "
                "TaxCalculation not found"
            )
        )

    return calculation


def _validate_tax_calculation(
    calculation: TaxCalculation,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> None:
    if (
        calculation.company_id
        != company_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "company mismatch"
        )

    if (
        calculation.id
        != tax_calculation_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "identity mismatch"
        )

    try:
        tax_type = TaxType(
            calculation.tax_type
        )
    except ValueError as exc:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "has unsupported tax type"
        ) from exc

    if tax_type != TaxType.VAT:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge requires "
            "a VAT TaxCalculation"
        )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "has unsupported direction"
        ) from exc

    if direction != TaxDirection.OUTPUT:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge requires "
            "an OUTPUT VAT TaxCalculation"
        )

    if (
        not isinstance(
            calculation.currency_code,
            str,
        )
        or len(
            calculation.currency_code
        )
        != 3
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "currency must contain exactly "
            "3 characters"
        )

    if (
        calculation.trade_document_id
        is None
        or calculation.trade_document_id
        <= 0
        or calculation.trade_document_line_id
        is None
        or calculation.trade_document_line_id
        <= 0
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "must belong to a valid Invoice line"
        )


async def _load_sales_recognition_events(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    SalesRecognitionEvent,
    ...,
]:
    """
    Load complete SalesRecognition history for the exact Invoice
    line represented by this TaxCalculation.

    ACTIVE and REVERSED allocation history remains visible.
    """

    return tuple(
        (
            await db.execute(
                select(
                    SalesRecognitionEvent
                )
                .join(
                    InvoiceFulfillmentAllocation,
                    and_(
                        (
                            InvoiceFulfillmentAllocation
                            .company_id
                            == (
                                SalesRecognitionEvent
                                .company_id
                            )
                        ),
                        (
                            InvoiceFulfillmentAllocation
                            .id
                            == (
                                SalesRecognitionEvent
                                .invoice_fulfillment_allocation_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        SalesRecognitionEvent
                        .company_id
                        == calculation.company_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .invoice_id
                        == (
                            calculation
                            .trade_document_id
                        )
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .invoice_line_id
                        == (
                            calculation
                            .trade_document_line_id
                        )
                    ),
                )
                .order_by(
                    SalesRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def _load_tax_recognition_events(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
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
                    (
                        TaxRecognitionEvent
                        .company_id
                        == calculation.company_id
                    ),
                    (
                        TaxRecognitionEvent
                        .tax_calculation_id
                        == calculation.id
                    ),
                )
                .order_by(
                    TaxRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def _load_bridge_events(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    VatAdvanceBridgeEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    VatAdvanceBridgeEvent
                )
                .where(
                    (
                        VatAdvanceBridgeEvent
                        .company_id
                        == calculation.company_id
                    ),
                    (
                        VatAdvanceBridgeEvent
                        .tax_calculation_id
                        == calculation.id
                    ),
                )
                .order_by(
                    VatAdvanceBridgeEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def reconcile_vat_advance_bridge_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> VatAdvanceBridgeReconciliationResult:
    """
    Reconcile financial-accounting VAT advance bridge state after
    Sales recognition and OUTPUT VAT tax recognition have reached
    their current immutable ledger state.

    IMPORTANT:
    This service does not post JournalEntries.

    It only derives desired bridge state and persists immutable
    VatAdvanceBridgeEvent rows.

    Lifecycle wiring must call this AFTER OUTPUT VAT reconciliation.
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

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    calculation = (
        await _load_tax_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    _validate_tax_calculation(
        calculation,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    sales_events = (
        await _load_sales_recognition_events(
            db,
            calculation=calculation,
        )
    )

    tax_events = (
        await _load_tax_recognition_events(
            db,
            calculation=calculation,
        )
    )

    bridge_events = (
        await _load_bridge_events(
            db,
            calculation=calculation,
        )
    )

    try:
        current_sales_targets = (
            build_current_sales_recognition_targets(
                events=sales_events,
                currency_code=(
                    calculation.currency_code
                ),
            )
        )

        current_tax_targets = (
            build_current_output_tax_recognition_targets(
                tax_events
            )
        )

        current_bridge_targets = (
            build_current_vat_advance_bridge_targets(
                events=bridge_events,
                currency_code=(
                    calculation.currency_code
                ),
            )
        )
    except (
        SalesRecognitionDataIntegrityError,
        TaxRecognitionDataIntegrityError,
    ) as exc:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge could not rebuild "
            "current Sales/Tax recognition state: "
            f"{exc}"
        ) from exc

    desired_targets = (
        build_vat_advance_bridge_reconciliation_targets(
            tax_calculation_id=(
                tax_calculation_id
            ),
            sales_targets=(
                current_sales_targets
            ),
            tax_targets=(
                current_tax_targets
            ),
            current_bridge_targets=(
                current_bridge_targets
            ),
            adjustment_date=(
                adjustment_date
            ),
            currency_code=(
                calculation.currency_code
            ),
        )
    )

    created_events = []

    for target in desired_targets:
        created_events.extend(
            await reconcile_vat_advance_bridge_source(
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

    return VatAdvanceBridgeReconciliationResult(
        tax_calculation_id=(
            tax_calculation_id
        ),
        current_sales_targets=(
            current_sales_targets
        ),
        current_tax_targets=(
            current_tax_targets
        ),
        current_bridge_targets=(
            current_bridge_targets
        ),
        desired_targets=(
            desired_targets
        ),
        created_events=tuple(
            created_events
        ),
    )
