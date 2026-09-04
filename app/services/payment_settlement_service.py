from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.payment_journal_service import (
    PaymentJournalCurrencyError,
    PaymentJournalError,
    generate_and_post_settlement_journal_entry,
    reverse_settlement_journal_entry,
)

from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)

from app.services.tax_recognition_lifecycle_service import (
    TaxRecognitionLifecycleError,
    reconcile_tax_for_invoice,
)

from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)

from app.services.customer_advance_clearing_lifecycle_service import (
    CustomerAdvanceClearingLifecycleError,
    reconcile_customer_advance_clearing_lifecycle_for_invoice,
)


ZERO = Decimal("0")


class PaymentSettlementError(Exception):
    """Base Payment settlement error."""


class PaymentSettlementNotFoundError(
    PaymentSettlementError
):
    pass


class PaymentSettlementPaymentStatusError(
    PaymentSettlementError
):
    pass


class PaymentSettlementOpenItemStatusError(
    PaymentSettlementError
):
    pass


class PaymentSettlementDirectionError(
    PaymentSettlementError
):
    pass


class PaymentSettlementCounterpartyError(
    PaymentSettlementError
):
    pass


class PaymentSettlementContractError(
    PaymentSettlementError
):
    pass


class PaymentSettlementCurrencyError(
    PaymentSettlementError
):
    pass


class PaymentSettlementAmountError(
    PaymentSettlementError
):
    pass


class PaymentOverAllocationError(
    PaymentSettlementError
):
    pass


class OpenItemOverAllocationError(
    PaymentSettlementError
):
    pass


class DuplicateActivePaymentSettlementError(
    PaymentSettlementError
):
    pass


class PaymentSettlementReversalStateError(
    PaymentSettlementError
):
    pass


class PaymentSettlementActorError(
    PaymentSettlementError
):
    pass


class PaymentSettlementDataIntegrityError(
    PaymentSettlementError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PaymentSettlementPlan:
    amount: Decimal

    payment_settled_before: Decimal
    payment_settled_after: Decimal

    open_item_settled_before: Decimal
    open_item_settled_after: Decimal

    open_item_status_after: (
        CounterpartyOpenItemStatus
    )


def get_expected_open_item_type(
    direction: PaymentDirection,
) -> CounterpartyOpenItemType:
    if (
        direction
        == PaymentDirection.INCOMING
    ):
        return (
            CounterpartyOpenItemType.RECEIVABLE
        )

    if (
        direction
        == PaymentDirection.OUTGOING
    ):
        return (
            CounterpartyOpenItemType.PAYABLE
        )

    raise PaymentSettlementDirectionError(
        "Unsupported Payment direction"
    )


def normalize_settlement_amount(
    *,
    amount: Decimal,
    currency_code: str,
) -> Decimal:
    normalized = round_currency_amount(
        amount=Decimal(
            str(amount)
        ),
        currency_code=currency_code,
    )

    if normalized <= ZERO:
        raise PaymentSettlementAmountError(
            "Settlement amount must be "
            "greater than zero"
        )

    return normalized


def calculate_open_item_status(
    *,
    original_amount: Decimal,
    settled_amount: Decimal,
) -> CounterpartyOpenItemStatus:
    original = Decimal(
        original_amount
    )

    settled = Decimal(
        settled_amount
    )

    if original <= ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Open item original amount must "
            "be greater than zero"
        )

    if settled < ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Settled amount cannot be negative"
        )

    if settled > original:
        raise OpenItemOverAllocationError(
            "Settled amount exceeds Open Item "
            "original amount"
        )

    if settled == ZERO:
        return (
            CounterpartyOpenItemStatus.OPEN
        )

    if settled == original:
        return (
            CounterpartyOpenItemStatus.SETTLED
        )

    return (
        CounterpartyOpenItemStatus.PARTIALLY_SETTLED
    )


def validate_payment_settlement_match(
    *,
    payment: Payment,
    open_item: CounterpartyOpenItem,
) -> None:
    if (
        payment.status
        != PaymentStatus.CONFIRMED
    ):
        raise (
            PaymentSettlementPaymentStatusError(
                "Only confirmed Payments can "
                "be allocated"
            )
        )

    if (
        open_item.status
        == CounterpartyOpenItemStatus.CANCELLED
    ):
        raise (
            PaymentSettlementOpenItemStatusError(
                "Cancelled Open Item cannot "
                "be settled"
            )
        )

    expected_type = (
        get_expected_open_item_type(
            payment.direction
        )
    )

    if (
        open_item.item_type
        != expected_type
    ):
        raise PaymentSettlementDirectionError(
            "Payment direction does not match "
            "Open Item type"
        )

    if (
        payment.counterparty_id
        != open_item.counterparty_id
    ):
        raise (
            PaymentSettlementCounterpartyError(
                "Payment counterparty does not "
                "match Open Item counterparty"
            )
        )

    if (
        payment.contract_id is not None
        and payment.contract_id
        != open_item.contract_id
    ):
        raise PaymentSettlementContractError(
            "Payment contract does not match "
            "Open Item contract"
        )

    if (
        payment.currency_code
        != open_item.currency_code
    ):
        raise PaymentSettlementCurrencyError(
            "Payment currency does not match "
            "Open Item currency"
        )


def create_payment_settlement_plan(
    *,
    payment: Payment,
    open_item: CounterpartyOpenItem,
    amount: Decimal,
    payment_settled_before: Decimal,
    open_item_settled_before: Decimal,
) -> PaymentSettlementPlan:
    normalized_amount = (
        normalize_settlement_amount(
            amount=amount,
            currency_code=(
                payment.currency_code
            ),
        )
    )

    payment_before = Decimal(
        payment_settled_before
    )

    open_item_before = Decimal(
        open_item_settled_before
    )

    if (
        payment_before < ZERO
        or open_item_before < ZERO
    ):
        raise PaymentSettlementDataIntegrityError(
            "Persisted settlement aggregate "
            "cannot be negative"
        )

    payment_after = (
        payment_before
        + normalized_amount
    )

    open_item_after = (
        open_item_before
        + normalized_amount
    )

    payment_amount = Decimal(
        payment.amount
    )

    open_item_amount = Decimal(
        open_item.original_amount
    )

    if (
        payment_after
        > payment_amount
    ):
        raise PaymentOverAllocationError(
            "ACTIVE settlement allocations "
            "exceed Payment amount"
        )

    if (
        open_item_after
        > open_item_amount
    ):
        raise OpenItemOverAllocationError(
            "ACTIVE settlement allocations "
            "exceed Open Item original amount"
        )

    status_after = (
        calculate_open_item_status(
            original_amount=(
                open_item_amount
            ),
            settled_amount=(
                open_item_after
            ),
        )
    )

    return PaymentSettlementPlan(
        amount=normalized_amount,
        payment_settled_before=(
            payment_before
        ),
        payment_settled_after=(
            payment_after
        ),
        open_item_settled_before=(
            open_item_before
        ),
        open_item_settled_after=(
            open_item_after
        ),
        open_item_status_after=(
            status_after
        ),
    )


async def get_locked_settlement_payment(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> Payment:
    payment = (
        await db.execute(
            select(
                Payment
            )
            .where(
                Payment.company_id
                == company_id,
                Payment.id
                == payment_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if payment is None:
        raise PaymentSettlementNotFoundError(
            "Payment not found"
        )

    return payment


async def get_locked_settlement_open_item(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_id: int,
) -> CounterpartyOpenItem:
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            )
            .where(
                CounterpartyOpenItem.company_id
                == company_id,
                CounterpartyOpenItem.id
                == open_item_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if item is None:
        raise PaymentSettlementNotFoundError(
            "Counterparty Open Item not found"
        )

    return item


async def get_active_payment_settled_amount(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> Decimal:
    value = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        PaymentSettlementAllocation.amount
                    ),
                    ZERO,
                )
            ).where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.payment_id
                == payment_id,
                PaymentSettlementAllocation.status
                == (
                    PaymentSettlementAllocationStatus.ACTIVE
                ),
            )
        )
    ).scalar_one()

    return Decimal(
        value
    )


async def get_active_open_item_settled_amount(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_id: int,
) -> Decimal:
    value = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        PaymentSettlementAllocation.amount
                    ),
                    ZERO,
                )
            ).where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.open_item_id
                == open_item_id,
                PaymentSettlementAllocation.status
                == (
                    PaymentSettlementAllocationStatus.ACTIVE
                ),
            )
        )
    ).scalar_one()

    return Decimal(
        value
    )


async def _active_settlement_pair_exists(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    open_item_id: int,
) -> bool:
    allocation_id = (
        await db.execute(
            select(
                PaymentSettlementAllocation.id
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.payment_id
                == payment_id,
                PaymentSettlementAllocation.open_item_id
                == open_item_id,
                PaymentSettlementAllocation.status
                == (
                    PaymentSettlementAllocationStatus.ACTIVE
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    return (
        allocation_id is not None
    )


async def has_active_payment_settlement_allocations(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    lock_rows: bool = False,
) -> bool:
    statement = (
        select(
            PaymentSettlementAllocation.id
        )
        .where(
            PaymentSettlementAllocation.company_id
            == company_id,
            PaymentSettlementAllocation.payment_id
            == payment_id,
            PaymentSettlementAllocation.status
            == (
                PaymentSettlementAllocationStatus.ACTIVE
            ),
        )
        .limit(1)
    )

    if lock_rows:
        statement = (
            statement.with_for_update()
        )

    allocation_id = (
        await db.execute(
            statement
        )
    ).scalar_one_or_none()

    return (
        allocation_id is not None
    )


async def create_payment_settlement_allocation(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    open_item_id: int,
    amount: Decimal,
    created_by: int,
) -> PaymentSettlementAllocation:
    """
    Create one ACTIVE monetary settlement.

    Lock order:
      1. Payment
      2. CounterpartyOpenItem
      3. ACTIVE allocation aggregate reads

    Caller owns COMMIT / ROLLBACK.
    """

    if created_by <= 0:
        raise PaymentSettlementActorError(
            "created_by must be greater than zero"
        )

    payment = (
        await get_locked_settlement_payment(
            db,
            company_id=company_id,
            payment_id=payment_id,
        )
    )

    open_item = (
        await get_locked_settlement_open_item(
            db,
            company_id=company_id,
            open_item_id=open_item_id,
        )
    )

    validate_payment_settlement_match(
        payment=payment,
        open_item=open_item,
    )

    if await _active_settlement_pair_exists(
        db,
        company_id=company_id,
        payment_id=payment.id,
        open_item_id=open_item.id,
    ):
        raise (
            DuplicateActivePaymentSettlementError(
                "ACTIVE settlement allocation "
                "for this Payment/Open Item "
                "pair already exists"
            )
        )

    payment_settled_before = (
        await get_active_payment_settled_amount(
            db,
            company_id=company_id,
            payment_id=payment.id,
        )
    )

    open_item_settled_before = (
        await get_active_open_item_settled_amount(
            db,
            company_id=company_id,
            open_item_id=open_item.id,
        )
    )

    plan = create_payment_settlement_plan(
        payment=payment,
        open_item=open_item,
        amount=amount,
        payment_settled_before=(
            payment_settled_before
        ),
        open_item_settled_before=(
            open_item_settled_before
        ),
    )

    allocation = (
        PaymentSettlementAllocation(
            company_id=company_id,
            payment_id=payment.id,
            open_item_id=open_item.id,
            amount=plan.amount,
            status=(
                PaymentSettlementAllocationStatus.ACTIVE
            ),
            created_by=created_by,
        )
    )

    db.add(
        allocation
    )

    open_item.status = (
        plan.open_item_status_after
    )

    await db.flush()

    adjustment_date = (
        datetime.now(
            timezone.utc
        ).date()
    )
    # PaymentSettlementAllocation is commercial-only.
    #
    # RECEIVABLE:
    # Dr 681 / Cr 361 belongs to CustomerAdvanceClearing and
    # must wait until economic SalesRecognitionEvent capacity
    # exists.
    #
    # PAYABLE:
    # Dr 631 / Cr 371 belongs to SupplierAdvanceClearing and
    # must wait until economic supplier liability exists.

    try:
        await reconcile_tax_for_invoice(
            db,
            company_id=company_id,
            invoice_id=(
                open_item.trade_document_id
            ),
            adjustment_date=(
                adjustment_date
            ),
            created_by=created_by,
        )
    except TaxRecognitionLifecycleError as exc:
        raise PaymentSettlementDataIntegrityError(
            "VAT recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    if (
        open_item.item_type
        == CounterpartyOpenItemType.RECEIVABLE
    ):
        try:
            await (
                reconcile_customer_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=(
                        open_item.trade_document_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        except CustomerAdvanceClearingLifecycleError as exc:
            raise PaymentSettlementDataIntegrityError(
                "Customer advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    if (
        open_item.item_type
        == CounterpartyOpenItemType.PAYABLE
    ):
        try:
            await (
                reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=(
                        open_item.trade_document_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        except SupplierAdvanceClearingLifecycleError as exc:
            raise PaymentSettlementDataIntegrityError(
                "Supplier advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    return allocation


async def _get_settlement_allocation_identity(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_id: int,
) -> tuple[int, int]:
    row = (
        await db.execute(
            select(
                PaymentSettlementAllocation.payment_id,
                PaymentSettlementAllocation.open_item_id,
            ).where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.id
                == allocation_id,
            )
        )
    ).one_or_none()

    if row is None:
        raise PaymentSettlementNotFoundError(
            "Payment settlement allocation "
            "not found"
        )

    return (
        row.payment_id,
        row.open_item_id,
    )


async def reverse_payment_settlement_allocation(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_id: int,
    reversed_by: int,
) -> PaymentSettlementAllocation:
    """
    Reverse one ACTIVE settlement allocation.

    Lock order intentionally matches creation:
      1. read immutable allocation identity
      2. lock Payment
      3. lock CounterpartyOpenItem
      4. lock allocation row

    Caller owns COMMIT / ROLLBACK.
    """

    if reversed_by <= 0:
        raise PaymentSettlementActorError(
            "reversed_by must be greater than zero"
        )

    (
        payment_id,
        open_item_id,
    ) = (
        await _get_settlement_allocation_identity(
            db,
            company_id=company_id,
            allocation_id=allocation_id,
        )
    )

    payment = (
        await get_locked_settlement_payment(
            db,
            company_id=company_id,
            payment_id=payment_id,
        )
    )

    open_item = (
        await get_locked_settlement_open_item(
            db,
            company_id=company_id,
            open_item_id=open_item_id,
        )
    )

    allocation = (
        await db.execute(
            select(
                PaymentSettlementAllocation
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.id
                == allocation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation is None:
        raise PaymentSettlementNotFoundError(
            "Payment settlement allocation "
            "not found"
        )

    if (
        allocation.payment_id
        != payment.id
        or allocation.open_item_id
        != open_item.id
    ):
        raise PaymentSettlementDataIntegrityError(
            "Payment settlement allocation "
            "identity changed unexpectedly"
        )

    if (
        allocation.status
        != PaymentSettlementAllocationStatus.ACTIVE
    ):
        raise (
            PaymentSettlementReversalStateError(
                "Only ACTIVE settlement allocation "
                "can be reversed"
            )
        )

    payment_settled_before = (
        await get_active_payment_settled_amount(
            db,
            company_id=company_id,
            payment_id=payment.id,
        )
    )

    open_item_settled_before = (
        await get_active_open_item_settled_amount(
            db,
            company_id=company_id,
            open_item_id=open_item.id,
        )
    )

    allocation_amount = Decimal(
        allocation.amount
    )

    if (
        payment_settled_before
        < allocation_amount
        or open_item_settled_before
        < allocation_amount
    ):
        raise PaymentSettlementDataIntegrityError(
            "ACTIVE settlement aggregate is "
            "smaller than allocation amount"
        )

    open_item_settled_after = (
        open_item_settled_before
        - allocation_amount
    )

    reversal_timestamp = (
        datetime.now(
            timezone.utc
        )
    )
    # Settlement reversal is commercial-only here.
    #
    # Customer/Supplier clearing reversal is reconciled only
    # after this allocation has become REVERSED and the new
    # commercial settlement state is durable in the transaction.

    allocation.status = (
        PaymentSettlementAllocationStatus.REVERSED
    )

    allocation.reversed_by = (
        reversed_by
    )

    allocation.reversed_at = (
        reversal_timestamp
    )

    open_item.status = (
        calculate_open_item_status(
            original_amount=(
                open_item.original_amount
            ),
            settled_amount=(
                open_item_settled_after
            ),
        )
    )

    await db.flush()

    adjustment_date = (
        allocation.reversed_at.date()
    )

    try:
        await reconcile_tax_for_invoice(
            db,
            company_id=company_id,
            invoice_id=(
                open_item.trade_document_id
            ),
            adjustment_date=(
                adjustment_date
            ),
            created_by=reversed_by,
        )
    except TaxRecognitionLifecycleError as exc:
        raise PaymentSettlementDataIntegrityError(
            "VAT recognition reconciliation "
            "failed: "
            f"{exc}"
        ) from exc

    if (
        open_item.item_type
        == CounterpartyOpenItemType.RECEIVABLE
    ):
        try:
            await (
                reconcile_customer_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=(
                        open_item.trade_document_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=reversed_by,
                )
            )
        except CustomerAdvanceClearingLifecycleError as exc:
            raise PaymentSettlementDataIntegrityError(
                "Customer advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    if (
        open_item.item_type
        == CounterpartyOpenItemType.PAYABLE
    ):
        try:
            await (
                reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=(
                        open_item.trade_document_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=reversed_by,
                )
            )
        except SupplierAdvanceClearingLifecycleError as exc:
            raise PaymentSettlementDataIntegrityError(
                "Supplier advance clearing "
                "lifecycle failed: "
                f"{exc}"
            ) from exc

    return allocation


@dataclass(
    frozen=True,
    slots=True,
)
class PaymentSettlementReconciliation:
    payment: Payment
    settled_amount: Decimal
    unallocated_amount: Decimal
    fully_allocated: bool
    allocations: tuple[
        PaymentSettlementAllocation,
        ...,
    ]


@dataclass(
    frozen=True,
    slots=True,
)
class CounterpartyOpenItemSettlementBalance:
    open_item: CounterpartyOpenItem
    settled_amount: Decimal
    open_amount: Decimal


def calculate_payment_unallocated_amount(
    *,
    payment_amount: Decimal,
    settled_amount: Decimal,
) -> Decimal:
    payment_total = Decimal(
        payment_amount
    )

    settled = Decimal(
        settled_amount
    )

    if payment_total <= ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Payment amount must be greater than zero"
        )

    if settled < ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Payment settled amount cannot be negative"
        )

    if settled > payment_total:
        raise PaymentSettlementDataIntegrityError(
            "Payment settled amount exceeds "
            "Payment amount"
        )

    return (
        payment_total
        - settled
    )


def calculate_open_item_open_amount(
    *,
    original_amount: Decimal,
    settled_amount: Decimal,
    status: CounterpartyOpenItemStatus,
) -> Decimal:
    original = Decimal(
        original_amount
    )

    settled = Decimal(
        settled_amount
    )

    if original <= ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Open Item original amount must "
            "be greater than zero"
        )

    if settled < ZERO:
        raise PaymentSettlementDataIntegrityError(
            "Open Item settled amount cannot "
            "be negative"
        )

    if settled > original:
        raise PaymentSettlementDataIntegrityError(
            "Open Item settled amount exceeds "
            "original amount"
        )

    if (
        status
        == CounterpartyOpenItemStatus.CANCELLED
    ):
        if settled != ZERO:
            raise PaymentSettlementDataIntegrityError(
                "Cancelled Open Item cannot have "
                "ACTIVE settlements"
            )

        return ZERO

    expected_status = (
        calculate_open_item_status(
            original_amount=original,
            settled_amount=settled,
        )
    )

    if status != expected_status:
        raise PaymentSettlementDataIntegrityError(
            "Open Item stored status does not "
            "match ACTIVE settlement balance"
        )

    return (
        original
        - settled
    )


async def get_active_payment_settled_amounts(
    db: AsyncSession,
    *,
    company_id: int,
    payment_ids: tuple[int, ...],
) -> dict[int, Decimal]:
    if not payment_ids:
        return {}

    rows = (
        await db.execute(
            select(
                PaymentSettlementAllocation.payment_id,
                func.sum(
                    PaymentSettlementAllocation.amount
                ),
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.payment_id.in_(
                    payment_ids
                ),
                PaymentSettlementAllocation.status
                == (
                    PaymentSettlementAllocationStatus.ACTIVE
                ),
            )
            .group_by(
                PaymentSettlementAllocation.payment_id
            )
        )
    ).all()

    return {
        int(payment_id): Decimal(
            amount
        )
        for payment_id, amount in rows
    }


async def get_active_open_item_settled_amounts(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_ids: tuple[int, ...],
) -> dict[int, Decimal]:
    if not open_item_ids:
        return {}

    rows = (
        await db.execute(
            select(
                PaymentSettlementAllocation.open_item_id,
                func.sum(
                    PaymentSettlementAllocation.amount
                ),
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.open_item_id.in_(
                    open_item_ids
                ),
                PaymentSettlementAllocation.status
                == (
                    PaymentSettlementAllocationStatus.ACTIVE
                ),
            )
            .group_by(
                PaymentSettlementAllocation.open_item_id
            )
        )
    ).all()

    return {
        int(open_item_id): Decimal(
            amount
        )
        for open_item_id, amount in rows
    }


async def get_payment_settlement_allocation_history(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> tuple[
    Payment,
    tuple[
        PaymentSettlementAllocation,
        ...,
    ],
]:
    payment = (
        await db.execute(
            select(
                Payment
            ).where(
                Payment.company_id
                == company_id,
                Payment.id
                == payment_id,
            )
        )
    ).scalar_one_or_none()

    if payment is None:
        raise PaymentSettlementNotFoundError(
            "Payment not found"
        )

    allocations = tuple(
        (
            await db.execute(
                select(
                    PaymentSettlementAllocation
                )
                .where(
                    PaymentSettlementAllocation.company_id
                    == company_id,
                    PaymentSettlementAllocation.payment_id
                    == payment_id,
                )
                .order_by(
                    PaymentSettlementAllocation.id
                )
            )
        ).scalars().all()
    )

    return (
        payment,
        allocations,
    )


async def get_payment_settlement_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> PaymentSettlementReconciliation:
    (
        payment,
        allocations,
    ) = (
        await get_payment_settlement_allocation_history(
            db,
            company_id=company_id,
            payment_id=payment_id,
        )
    )

    settled_amount = (
        await get_active_payment_settled_amount(
            db,
            company_id=company_id,
            payment_id=payment.id,
        )
    )

    unallocated_amount = (
        calculate_payment_unallocated_amount(
            payment_amount=payment.amount,
            settled_amount=settled_amount,
        )
    )

    return PaymentSettlementReconciliation(
        payment=payment,
        settled_amount=settled_amount,
        unallocated_amount=(
            unallocated_amount
        ),
        fully_allocated=(
            unallocated_amount
            == ZERO
        ),
        allocations=allocations,
    )


async def get_open_item_settlement_balance(
    db: AsyncSession,
    *,
    company_id: int,
    open_item_id: int,
) -> CounterpartyOpenItemSettlementBalance:
    open_item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            ).where(
                CounterpartyOpenItem.company_id
                == company_id,
                CounterpartyOpenItem.id
                == open_item_id,
            )
        )
    ).scalar_one_or_none()

    if open_item is None:
        raise PaymentSettlementNotFoundError(
            "Counterparty Open Item not found"
        )

    settled_amount = (
        await get_active_open_item_settled_amount(
            db,
            company_id=company_id,
            open_item_id=open_item.id,
        )
    )

    open_amount = (
        calculate_open_item_open_amount(
            original_amount=(
                open_item.original_amount
            ),
            settled_amount=settled_amount,
            status=open_item.status,
        )
    )

    return (
        CounterpartyOpenItemSettlementBalance(
            open_item=open_item,
            settled_amount=settled_amount,
            open_amount=open_amount,
        )
    )
