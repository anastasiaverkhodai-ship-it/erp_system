from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.customer_advance_clearing_calculation_service import (
    CustomerAdvanceClearingCalculationError,
    CustomerAdvanceClearingTarget,
    CustomerAdvanceSettlementCandidate,
    build_customer_advance_clearing_targets,
    money,
)
from app.services.customer_advance_clearing_persistence_service import (
    CustomerAdvanceClearingDataIntegrityError,
    build_current_customer_advance_clearing_targets,
    reconcile_customer_advance_clearing_source,
)
from app.services.customer_economic_receivable_loader_service import (
    CustomerEconomicReceivableLoaderError,
    load_customer_economic_receivable_candidates_for_invoice,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


ZERO = Decimal("0.00")


class CustomerAdvanceClearingReconciliationError(
    Exception
):
    """Base invoice-level Customer Advance Clearing error."""


class CustomerAdvanceClearingInvoiceNotFoundError(
    CustomerAdvanceClearingReconciliationError
):
    """RECEIVABLE Open Item for invoice was not found."""


class CustomerAdvanceClearingReconciliationDataIntegrityError(
    CustomerAdvanceClearingReconciliationError
):
    """Invoice clearing state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceClearingReconciliationResult:
    invoice_id: int
    open_item_id: int
    settlement_candidates: tuple
    receivable_candidates: tuple
    current_targets: tuple[
        CustomerAdvanceClearingTarget,
        ...,
    ]
    desired_targets: tuple[
        CustomerAdvanceClearingTarget,
        ...,
    ]
    reconciliation_targets: tuple[
        CustomerAdvanceClearingTarget,
        ...,
    ]
    created_events: tuple[
        CustomerAdvanceClearingEvent,
        ...,
    ]


def _positive_int(
    value,
    *,
    label: str,
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
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            f"{label} must be a positive integer"
        )

    return value


def _currency(
    value,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Currency must be a string"
        )

    normalized = (
        value
        .strip()
        .upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Currency must be a 3-letter code"
        )

    return normalized


def _target_pair(
    target: CustomerAdvanceClearingTarget,
) -> tuple[
    int,
    int,
]:
    return (
        target.settlement_source_id,
        target.receivable_source_id,
    )


def _target_map(
    targets: Iterable[
        CustomerAdvanceClearingTarget
    ],
    *,
    label: str,
) -> dict[
    tuple[int, int],
    CustomerAdvanceClearingTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            CustomerAdvanceClearingTarget,
        ):
            raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                f"{label} contains invalid target type"
            )

        pair = _target_pair(
            target
        )

        if pair in result:
            raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                f"{label} contains duplicate source pair"
            )

        result[
            pair
        ] = target

    return result


def build_customer_advance_clearing_reconciliation_targets(
    *,
    desired_targets: Iterable[
        CustomerAdvanceClearingTarget
    ],
    current_targets: Iterable[
        CustomerAdvanceClearingTarget
    ],
) -> tuple[
    CustomerAdvanceClearingTarget,
    ...,
]:
    """
    Build persistence targets required to transform current
    immutable clearing state into desired FIFO state.

    Ordering rule:

        removals / decreases
        BEFORE
        additions / increases

    This avoids temporarily increasing aggregate clearing while
    an obsolete pair is still active.

    Exact targets are omitted.
    """
    desired = _target_map(
        desired_targets,
        label="desired_targets",
    )

    current = _target_map(
        current_targets,
        label="current_targets",
    )

    decreases = []
    increases = []

    all_pairs = (
        set(
            desired
        )
        | set(
            current
        )
    )

    for pair in all_pairs:
        wanted = desired.get(
            pair
        )

        existing = current.get(
            pair
        )

        if (
            wanted is not None
            and existing is not None
        ):
            if (
                wanted.currency_code
                != existing.currency_code
            ):
                raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                    "Existing and desired source pair "
                    "currency differs"
                )

            if (
                wanted.event_date
                != existing.event_date
            ):
                raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                    "Existing and desired source pair "
                    "provenance date differs"
                )

            wanted_amount = money(
                wanted.amount
            )

            existing_amount = money(
                existing.amount
            )

            if (
                wanted_amount
                == existing_amount
            ):
                continue

            if (
                wanted_amount
                < existing_amount
            ):
                decreases.append(
                    wanted
                )
            else:
                increases.append(
                    wanted
                )

            continue

        if (
            existing is not None
            and wanted is None
        ):
            decreases.append(
                CustomerAdvanceClearingTarget(
                    settlement_source_id=(
                        existing
                        .settlement_source_id
                    ),
                    receivable_source_id=(
                        existing
                        .receivable_source_id
                    ),
                    event_date=(
                        existing.event_date
                    ),
                    amount=ZERO,
                    currency_code=(
                        existing.currency_code
                    ),
                )
            )

            continue

        if (
            wanted is not None
            and existing is None
        ):
            increases.append(
                wanted
            )

    decreases.sort(
        key=lambda target: (
            target.event_date,
            target.settlement_source_id,
            target.receivable_source_id,
        )
    )

    increases.sort(
        key=lambda target: (
            target.event_date,
            target.settlement_source_id,
            target.receivable_source_id,
        )
    )

    return tuple(
        decreases
        + increases
    )


async def _load_receivable_open_item(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> CounterpartyOpenItem:
    rows = (
        (
            await db.execute(
                select(
                    CounterpartyOpenItem
                )
                .where(
                    CounterpartyOpenItem.company_id
                    == company_id,
                    CounterpartyOpenItem.trade_document_id
                    == invoice_id,
                    CounterpartyOpenItem.item_type
                    == CounterpartyOpenItemType.RECEIVABLE,
                )
                .order_by(
                    CounterpartyOpenItem.id
                )
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        raise CustomerAdvanceClearingInvoiceNotFoundError(
            "RECEIVABLE Open Item for invoice not found"
        )

    if len(rows) != 1:
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Invoice has more than one RECEIVABLE Open Item"
        )

    return rows[0]


async def _load_customer_settlement_candidates(
    db: AsyncSession,
    *,
    company_id: int,
    open_item: CounterpartyOpenItem,
    currency_code: str,
) -> tuple[
    CustomerAdvanceSettlementCandidate,
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
                    == PaymentSettlementAllocation.company_id,
                    Payment.id
                    == PaymentSettlementAllocation.payment_id,
                ),
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.open_item_id
                == open_item.id,
                PaymentSettlementAllocation.status
                == PaymentSettlementAllocationStatus.ACTIVE,
                Payment.direction
                == PaymentDirection.INCOMING,
                Payment.status
                == PaymentStatus.CONFIRMED,
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
            _currency(
                payment.currency_code
            )
            != currency_code
        ):
            raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                "Settlement Payment currency differs "
                "from invoice Open Item currency"
            )

        amount = money(
            allocation.amount
        )

        if amount <= ZERO:
            raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                "ACTIVE settlement allocation amount "
                "must be greater than zero"
            )

        candidates.append(
            CustomerAdvanceSettlementCandidate(
                source_id=allocation.id,
                event_date=payment.payment_date,
                amount=amount,
                currency_code=currency_code,
            )
        )

    return tuple(
        candidates
    )


async def _load_customer_clearing_history(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_id: int,
) -> tuple[
    CustomerAdvanceClearingEvent,
    ...,
]:
    """
    Scope immutable clearing history by the commercial
    settlement's RECEIVABLE Open Item.

    This includes historical source pairs whose
    SalesRecognitionEvent has since been reversed.
    """
    return tuple(
        (
            await db.execute(
                select(
                    CustomerAdvanceClearingEvent
                )
                .join(
                    PaymentSettlementAllocation,
                    and_(
                        (
                            PaymentSettlementAllocation
                            .company_id
                            == (
                                CustomerAdvanceClearingEvent
                                .company_id
                            )
                        ),
                        (
                            PaymentSettlementAllocation
                            .id
                            == (
                                CustomerAdvanceClearingEvent
                                .payment_settlement_allocation_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        CustomerAdvanceClearingEvent
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
                    CustomerAdvanceClearingEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def reconcile_customer_advance_clearing_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    created_by: int,
    adjustment_date: date | None = None,
) -> CustomerAdvanceClearingReconciliationResult:
    """
    Reconcile complete customer-advance clearing state for one
    Sales Invoice.

    Commercial capacity:
        ACTIVE PaymentSettlementAllocation
        +
        CONFIRMED INCOMING Payment

    Economic 361 capacity:
        ACTIVE SalesRecognitionEvent
        recognized_gross_amount

    Accounting target:
        Dr 681
        Cr 361

    Tax recognition remains separate.

    Caller owns transaction COMMIT / ROLLBACK.
    """
    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    invoice_id = _positive_int(
        invoice_id,
        label="invoice_id",
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
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "adjustment_date must be a date"
        )

    open_item = (
        await _load_receivable_open_item(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
        )
    )

    currency_code = _currency(
        open_item.currency_code
    )

    settlement_candidates = (
        await _load_customer_settlement_candidates(
            db,
            company_id=company_id,
            open_item=open_item,
            currency_code=currency_code,
        )
    )

    try:
        receivable_candidates = (
            await load_customer_economic_receivable_candidates_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
            )
        )
    except (
        CustomerEconomicReceivableLoaderError
    ) as exc:
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Could not load active economic "
            f"customer receivables: {exc}"
        ) from exc

    for candidate in (
        receivable_candidates
    ):
        if (
            candidate.currency_code
            != currency_code
        ):
            raise CustomerAdvanceClearingReconciliationDataIntegrityError(
                "Economic receivable currency differs "
                "from invoice Open Item currency"
            )

    try:
        desired_targets = (
            build_customer_advance_clearing_targets(
                settlements=(
                    settlement_candidates
                ),
                receivables=(
                    receivable_candidates
                ),
                currency_code=currency_code,
            )
        )
    except (
        CustomerAdvanceClearingCalculationError
    ) as exc:
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Customer advance clearing calculation "
            f"failed: {exc}"
        ) from exc

    history = (
        await _load_customer_clearing_history(
            db,
            company_id=company_id,
            open_item_id=open_item.id,
        )
    )

    try:
        current_targets = (
            build_current_customer_advance_clearing_targets(
                events=history,
                currency_code=currency_code,
            )
        )
    except (
        CustomerAdvanceClearingDataIntegrityError
    ) as exc:
        raise CustomerAdvanceClearingReconciliationDataIntegrityError(
            "Could not rebuild current customer "
            f"clearing state: {exc}"
        ) from exc

    reconciliation_targets = (
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=desired_targets,
            current_targets=current_targets,
        )
    )

    created_events = []

    for target in (
        reconciliation_targets
    ):
        created_events.extend(
            await reconcile_customer_advance_clearing_source(
                db,
                company_id=company_id,
                target=target,
                currency_code=currency_code,
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

    return CustomerAdvanceClearingReconciliationResult(
        invoice_id=invoice_id,
        open_item_id=open_item.id,
        settlement_candidates=(
            settlement_candidates
        ),
        receivable_candidates=(
            receivable_candidates
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
