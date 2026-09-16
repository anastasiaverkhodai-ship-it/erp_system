from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_registration_event import (
    TaxInvoiceRegistrationEvent,
)


class TaxInvoiceRegistrationError(Exception):
    """Base tax-invoice registration lifecycle error."""


class TaxInvoiceRegistrationTransitionError(
    TaxInvoiceRegistrationError
):
    """Requested registration transition is not allowed."""


_ALLOWED = {
    "prepared": {
        "submitted",
        "registered",
        "suspended",
        "rejected",
    },
    "submitted": {
        "registered",
        "suspended",
        "rejected",
    },
    "suspended": {
        "submitted",
        "registered",
        "rejected",
    },
    "registered": set(),
    "rejected": set(),
}


def validate_tax_invoice_registration_transition(
    *,
    previous_status: str | None,
    new_status: str,
    direction: str = "output",
) -> None:
    if new_status not in _ALLOWED:
        raise TaxInvoiceRegistrationTransitionError(
            f"unsupported registration status={new_status!r}"
        )

    if previous_status is None:
        if direction == "output":
            if new_status != "prepared":
                raise TaxInvoiceRegistrationTransitionError(
                    "first OUTPUT registration event must be 'prepared'"
                )
            return

        if direction == "input":
            if new_status != "registered":
                raise TaxInvoiceRegistrationTransitionError(
                    "10.6 INPUT V1 must start with a registered "
                    "attested tax invoice"
                )
            return

        raise TaxInvoiceRegistrationTransitionError(
            "unsupported tax-invoice direction"
        )

    if previous_status not in _ALLOWED:
        raise TaxInvoiceRegistrationTransitionError(
            "stored registration status is invalid"
        )

    if new_status not in _ALLOWED[
        previous_status
    ]:
        raise TaxInvoiceRegistrationTransitionError(
            f"registration transition "
            f"{previous_status!r} -> {new_status!r} "
            "is not allowed"
        )


def _reference(
    *,
    status: str,
    reference: str | None,
) -> str | None:
    if reference is None:
        if status == "prepared":
            return None

        raise TaxInvoiceRegistrationError(
            "registration reference is required "
            "after prepared state"
        )

    normalized = reference.strip()

    if not normalized:
        raise TaxInvoiceRegistrationError(
            "registration reference cannot be blank"
        )

    if len(normalized) > 500:
        raise TaxInvoiceRegistrationError(
            "registration reference cannot exceed 500 characters"
        )

    return normalized


async def append_tax_invoice_registration_event(
    db: AsyncSession,
    *,
    company_id: int,
    tax_invoice_id: int,
    status: str,
    event_date: date,
    reference: str | None,
    created_by: int,
) -> TaxInvoiceRegistrationEvent:
    """
    Append one immutable registration-history event.

    Exact repetition of the latest event is idempotent.
    Caller owns commit/rollback.
    """

    if company_id <= 0:
        raise TaxInvoiceRegistrationError(
            "company_id must be positive"
        )

    if tax_invoice_id <= 0:
        raise TaxInvoiceRegistrationError(
            "tax_invoice_id must be positive"
        )

    if created_by <= 0:
        raise TaxInvoiceRegistrationError(
            "created_by must be positive"
        )

    normalized_status = status.strip().lower()
    normalized_reference = _reference(
        status=normalized_status,
        reference=reference,
    )

    invoice = await db.scalar(
        select(TaxInvoice)
        .where(
            TaxInvoice.id == tax_invoice_id,
            TaxInvoice.company_id == company_id,
        )
        .with_for_update()
    )

    if invoice is None:
        raise TaxInvoiceRegistrationError(
            "tax invoice does not belong to the company "
            "or does not exist"
        )

    latest = await db.scalar(
        select(TaxInvoiceRegistrationEvent)
        .where(
            TaxInvoiceRegistrationEvent.company_id
            == company_id,
            TaxInvoiceRegistrationEvent.tax_invoice_id
            == tax_invoice_id,
        )
        .order_by(
            TaxInvoiceRegistrationEvent.event_date.desc(),
            TaxInvoiceRegistrationEvent.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )

    if latest is not None:
        if event_date < latest.event_date:
            raise TaxInvoiceRegistrationError(
                "registration history cannot be backdated "
                "before its latest event"
            )

        if (
            latest.status == normalized_status
            and latest.event_date == event_date
            and latest.reference
            == normalized_reference
        ):
            return latest

    validate_tax_invoice_registration_transition(
        previous_status=(
            latest.status
            if latest is not None
            else None
        ),
        new_status=normalized_status,
        direction=invoice.direction,
    )

    event = TaxInvoiceRegistrationEvent(
        company_id=company_id,
        tax_invoice_id=tax_invoice_id,
        status=normalized_status,
        event_date=event_date,
        reference=normalized_reference,
        created_by=created_by,
    )

    db.add(event)
    await db.flush()

    return event
