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


def build_active_customer_economic_receivable_candidates(
    *,
    events: Iterable,
    company_id: int,
) -> tuple[
    CustomerEconomicReceivableCandidate,
    ...,
]:
    """
    Reconstruct ACTIVE economic customer receivables from
    immutable SalesRecognitionEvent history.

    An ACTIVE economic receivable is an original
    SalesRecognitionEvent whose id is not referenced by any
    valid reversal event.

    The economic 361 capacity is the original event's
    recognized_gross_amount.

    Replacement events are separate originals and therefore
    become separate economic sources.
    """
    company_id = _positive_context_id(
        company_id,
        label="company_id",
    )

    history = tuple(
        events
    )

    if not history:
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

        reversal_id, reversal_source_id = (
            _event_identity(
                event
            )
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

    candidates = []

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

        event_id, _ = _event_identity(
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

    Deliberately load both originals and reversals. Filtering
    only on reversal_of_id IS NULL would incorrectly include
    historical originals that have already been reversed.
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

    return (
        build_active_customer_economic_receivable_candidates(
            events=events,
            company_id=company_id,
        )
    )
