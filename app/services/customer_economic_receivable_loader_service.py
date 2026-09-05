from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)

from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.services.customer_advance_clearing_calculation_service import (
    CustomerEconomicReceivableCandidate,
    money,
)


class CustomerEconomicReceivableLoaderError(
    Exception
):
    """Base economic customer-receivable loader error."""


class CustomerEconomicReceivableLoaderContextError(
    CustomerEconomicReceivableLoaderError
):
    """Loader company / invoice context is invalid."""


class CustomerEconomicReceivableLoaderDataIntegrityError(
    CustomerEconomicReceivableLoaderError
):
    """Sales Recognition history is inconsistent."""


def _positive_int(
    value: int,
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
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            f"{label} must be a positive integer"
        )

    return value


def _positive_context_id(
    value: int,
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
        raise CustomerEconomicReceivableLoaderContextError(
            f"{label} must be a positive integer"
        )

    return value


def _currency(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Recognition currency must be a string"
        )

    normalized = (
        value
        .strip()
        .upper()
    )

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Recognition currency must be "
            "a 3-letter code"
        )

    return normalized


def _gross_amount(
    value: Decimal,
) -> Decimal:
    try:
        amount = money(
            Decimal(
                str(value)
            )
        )
    except Exception as exc:
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Recognition gross amount is invalid"
        ) from exc

    if amount <= Decimal(
        "0.00"
    ):
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Recognition gross amount "
            "must be greater than zero"
        )

    return amount


def _recognition_date(
    value: date,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Recognition recognition_date "
            "must be a date"
        )

    return value


def _event_identity(
    event,
) -> tuple[
    int,
    int,
]:
    event_id = _positive_int(
        getattr(
            event,
            "id",
            None,
        ),
        label=(
            "Sales Recognition event id"
        ),
    )

    fulfillment_source_id = (
        _positive_int(
            getattr(
                event,
                "invoice_fulfillment_allocation_id",
                None,
            ),
            label=(
                "Sales Recognition "
                "fulfillment source id"
            ),
        )
    )

    return (
        event_id,
        fulfillment_source_id,
    )


def _sales_return_gross_amount(
    value: Decimal,
) -> Decimal:
    try:
        amount = money(
            Decimal(
                str(
                    value
                )
            )
        )
    except Exception as exc:
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Return recognition gross amount is invalid"
        ) from exc

    if amount <= Decimal(
        "0.00"
    ):
        raise CustomerEconomicReceivableLoaderDataIntegrityError(
            "Sales Return recognition gross amount "
            "must be greater than zero"
        )

    return amount


def build_active_customer_economic_receivable_candidates(
    *,
    events: Iterable,
    company_id: int,
    sales_return_events: Iterable = (),
) -> tuple[
    CustomerEconomicReceivableCandidate,
    ...,
]:
    """
    Reconstruct ACTIVE economic customer receivables from
    immutable SalesRecognitionEvent history.

    Gross 361 capacity comes from active SalesRecognitionEvent
    recognized_gross_amount.

    Current active SalesReturnRecognitionEvent gross amounts
    reduce that exact SalesRecognitionEvent capacity.

    Immutable return reversals restore capacity because a reversed
    return original is no longer part of active return state.
    """

    company_id = _positive_context_id(
        company_id,
        label="company_id",
    )

    history = tuple(
        events
    )

    return_history = tuple(
        sales_return_events
    )

    if not history:
        if return_history:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition history exists "
                "without Sales Recognition history"
            )

        return ()

    event_by_id = {}

    for event in history:
        event_company_id = _positive_int(
            getattr(
                event,
                "company_id",
                None,
            ),
            label=(
                "Sales Recognition company_id"
            ),
        )

        if event_company_id != company_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition event company "
                "does not match loader company"
            )

        (
            event_id,
            _,
        ) = _event_identity(
            event
        )

        if event_id in event_by_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition history contains "
                "duplicate event id"
            )

        event_by_id[
            event_id
        ] = event

    reversed_original_ids = set()

    for event in history:
        reversal_of_id = getattr(
            event,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        (
            reversal_id,
            reversal_source_id,
        ) = _event_identity(
            event
        )

        original_id = _positive_int(
            reversal_of_id,
            label=(
                "Sales Recognition reversal_of_id"
            ),
        )

        if original_id == reversal_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition event cannot "
                "reverse itself"
            )

        original = event_by_id.get(
            original_id
        )

        if original is None:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition reversal references "
                "an original outside loaded history"
            )

        if (
            getattr(
                original,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition reversal must "
                "reference an original event"
            )

        (
            _,
            original_source_id,
        ) = _event_identity(
            original
        )

        if (
            reversal_source_id
            != original_source_id
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition reversal fulfillment "
                "source differs from original"
            )

        original_currency = _currency(
            getattr(
                original,
                "currency_code",
                None,
            )
        )

        reversal_currency = _currency(
            getattr(
                event,
                "currency_code",
                None,
            )
        )

        if (
            reversal_currency
            != original_currency
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition reversal currency "
                "differs from original"
            )

        original_gross = _gross_amount(
            getattr(
                original,
                "recognized_gross_amount",
                None,
            )
        )

        reversal_gross = _gross_amount(
            getattr(
                event,
                "recognized_gross_amount",
                None,
            )
        )

        if (
            reversal_gross
            != original_gross
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition reversal gross amount "
                "differs from original"
            )

        if original_id in reversed_original_ids:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Recognition original has more "
                "than one reversal in loaded history"
            )

        reversed_original_ids.add(
            original_id
        )

    active_sales = {}

    for event in history:
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        (
            event_id,
            _,
        ) = _event_identity(
            event
        )

        if event_id in reversed_original_ids:
            continue

        event_date = _recognition_date(
            getattr(
                event,
                "recognition_date",
                None,
            )
        )

        amount = _gross_amount(
            getattr(
                event,
                "recognized_gross_amount",
                None,
            )
        )

        currency_code = _currency(
            getattr(
                event,
                "currency_code",
                None,
            )
        )

        active_sales[
            event_id
        ] = (
            event,
            event_date,
            amount,
            currency_code,
        )

    return_event_by_id = {}

    for event in return_history:
        event_company_id = _positive_int(
            getattr(
                event,
                "company_id",
                None,
            ),
            label=(
                "Sales Return recognition company_id"
            ),
        )

        if event_company_id != company_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition event company "
                "does not match loader company"
            )

        event_id = _positive_int(
            getattr(
                event,
                "id",
                None,
            ),
            label=(
                "Sales Return recognition event id"
            ),
        )

        _positive_int(
            getattr(
                event,
                "trade_return_event_id",
                None,
            ),
            label="trade_return_event_id",
        )

        _positive_int(
            getattr(
                event,
                "sales_recognition_event_id",
                None,
            ),
            label="sales_recognition_event_id",
        )

        _recognition_date(
            getattr(
                event,
                "recognition_date",
                None,
            )
        )

        _currency(
            getattr(
                event,
                "currency_code",
                None,
            )
        )

        _sales_return_gross_amount(
            getattr(
                event,
                "returned_gross_amount",
                None,
            )
        )

        if event_id in return_event_by_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition history contains "
                "duplicate event id"
            )

        return_event_by_id[
            event_id
        ] = event

    reversed_return_ids = set()

    for event in return_history:
        reversal_of_id = getattr(
            event,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        reversal_id = _positive_int(
            getattr(
                event,
                "id",
                None,
            ),
            label=(
                "Sales Return recognition reversal id"
            ),
        )

        original_id = _positive_int(
            reversal_of_id,
            label=(
                "Sales Return recognition reversal_of_id"
            ),
        )

        if original_id == reversal_id:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition event cannot "
                "reverse itself"
            )

        original = return_event_by_id.get(
            original_id
        )

        if original is None:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal references "
                "an original outside loaded history"
            )

        if (
            getattr(
                original,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal must "
                "reference an original event"
            )

        if (
            getattr(
                event,
                "trade_return_event_id",
                None,
            )
            != getattr(
                original,
                "trade_return_event_id",
                None,
            )
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal changed "
                "TradeReturnEvent provenance"
            )

        if (
            getattr(
                event,
                "sales_recognition_event_id",
                None,
            )
            != getattr(
                original,
                "sales_recognition_event_id",
                None,
            )
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal changed "
                "SalesRecognitionEvent provenance"
            )

        if (
            _currency(
                getattr(
                    event,
                    "currency_code",
                    None,
                )
            )
            != _currency(
                getattr(
                    original,
                    "currency_code",
                    None,
                )
            )
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal currency "
                "differs from original"
            )

        if (
            _sales_return_gross_amount(
                getattr(
                    event,
                    "returned_gross_amount",
                    None,
                )
            )
            != _sales_return_gross_amount(
                getattr(
                    original,
                    "returned_gross_amount",
                    None,
                )
            )
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal gross "
                "amount differs from original"
            )

        if (
            _recognition_date(
                getattr(
                    event,
                    "recognition_date",
                    None,
                )
            )
            < _recognition_date(
                getattr(
                    original,
                    "recognition_date",
                    None,
                )
            )
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition reversal date "
                "precedes original"
            )

        if original_id in reversed_return_ids:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition original has "
                "more than one reversal"
            )

        reversed_return_ids.add(
            original_id
        )

    return_reductions = {}

    for event in return_history:
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        event_id = _positive_int(
            getattr(
                event,
                "id",
                None,
            ),
            label=(
                "Sales Return recognition event id"
            ),
        )

        if event_id in reversed_return_ids:
            continue

        sales_source_id = _positive_int(
            getattr(
                event,
                "sales_recognition_event_id",
                None,
            ),
            label="sales_recognition_event_id",
        )

        active_sales_source = active_sales.get(
            sales_source_id
        )

        if active_sales_source is None:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Active Sales Return recognition references "
                "an inactive SalesRecognitionEvent"
            )

        (
            _,
            sales_date,
            _,
            sales_currency,
        ) = active_sales_source

        return_date = _recognition_date(
            getattr(
                event,
                "recognition_date",
                None,
            )
        )

        if return_date < sales_date:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition precedes "
                "SalesRecognitionEvent"
            )

        return_currency = _currency(
            getattr(
                event,
                "currency_code",
                None,
            )
        )

        if (
            return_currency
            != sales_currency
        ):
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Sales Return recognition currency differs "
                "from SalesRecognitionEvent"
            )

        returned_amount = (
            _sales_return_gross_amount(
                getattr(
                    event,
                    "returned_gross_amount",
                    None,
                )
            )
        )

        return_reductions[
            sales_source_id
        ] = money(
            return_reductions.get(
                sales_source_id,
                Decimal(
                    "0.00"
                ),
            )
            + returned_amount
        )

    candidates = []

    for (
        event_id,
        (
            _,
            event_date,
            gross_amount,
            currency_code,
        ),
    ) in active_sales.items():
        returned_amount = (
            return_reductions.get(
                event_id,
                Decimal(
                    "0.00"
                ),
            )
        )

        if returned_amount > gross_amount:
            raise CustomerEconomicReceivableLoaderDataIntegrityError(
                "Active Sales Return gross amount exceeds "
                "SalesRecognitionEvent economic "
                "receivable capacity"
            )

        amount = money(
            gross_amount
            - returned_amount
        )

        if amount == Decimal(
            "0.00"
        ):
            continue

        candidates.append(
            CustomerEconomicReceivableCandidate(
                source_id=event_id,
                event_date=event_date,
                amount=amount,
                currency_code=currency_code,
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


async def load_customer_economic_receivable_candidates_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> tuple[
    CustomerEconomicReceivableCandidate,
    ...,
]:
    """
    Load immutable SalesRecognitionEvent history belonging to
    one Sales Invoice and derive its currently ACTIVE economic
    customer-receivable capacities.

    Active SalesReturnRecognitionEvent gross amounts reduce the
    exact SalesRecognitionEvent 361 capacity.

    Both original and reversal histories are deliberately loaded.
    """

    company_id = _positive_context_id(
        company_id,
        label="company_id",
    )

    invoice_id = _positive_context_id(
        invoice_id,
        label="invoice_id",
    )

    events = (
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
                        == company_id
                    ),
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
                    SalesRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )

    sales_return_events = (
        (
            await db.execute(
                select(
                    SalesReturnRecognitionEvent
                )
                .join(
                    SalesRecognitionEvent,
                    and_(
                        (
                            SalesRecognitionEvent.company_id
                            == (
                                SalesReturnRecognitionEvent
                                .company_id
                            )
                        ),
                        (
                            SalesRecognitionEvent.id
                            == (
                                SalesReturnRecognitionEvent
                                .sales_recognition_event_id
                            )
                        ),
                    ),
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
                            InvoiceFulfillmentAllocation.id
                            == (
                                SalesRecognitionEvent
                                .invoice_fulfillment_allocation_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        SalesReturnRecognitionEvent
                        .company_id
                        == company_id
                    ),
                    (
                        SalesRecognitionEvent
                        .company_id
                        == company_id
                    ),
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
                    SalesReturnRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )

    return (
        build_active_customer_economic_receivable_candidates(
            events=events,
            company_id=company_id,
            sales_return_events=(
                sales_return_events
            ),
        )
    )
