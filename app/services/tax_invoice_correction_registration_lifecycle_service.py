from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_correction import (
    TaxInvoiceCorrection,
)
from app.models.tax_invoice_correction_registration_event import (
    TaxInvoiceCorrectionRegistrationEvent,
)


RK_REGISTRATION_STATUSES = frozenset(
    {
        "prepared",
        "submitted",
        "registered",
        "suspended",
        "rejected",
    }
)

RK_REGISTRATION_TRANSITIONS = {
    "prepared": frozenset(
        {
            "submitted",
            "registered",
            "suspended",
            "rejected",
        }
    ),
    "submitted": frozenset(
        {
            "registered",
            "suspended",
            "rejected",
        }
    ),
    "suspended": frozenset(
        {
            "submitted",
            "registered",
            "rejected",
        }
    ),
    "registered": frozenset(),
    "rejected": frozenset(),
}

MAX_RK_REGISTRATION_AGE_DAYS = 1095


class TaxInvoiceCorrectionRegistrationError(Exception):
    """Base RK registration lifecycle error."""


class TaxInvoiceCorrectionRegistrationTransitionError(
    TaxInvoiceCorrectionRegistrationError
):
    """Illegal append-only RK registration transition."""


class TaxInvoiceCorrectionRegistrationAgeError(
    TaxInvoiceCorrectionRegistrationError
):
    """RK registration falls outside the legal PN age window."""


def _positive_int(
    value: int,
    *,
    field: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise TaxInvoiceCorrectionRegistrationError(
            f"{field} must be a positive integer"
        )

    return value


def _normalize_status(
    value: object,
) -> str:
    normalized = str(
        value
    ).strip().lower()

    if normalized not in RK_REGISTRATION_STATUSES:
        raise TaxInvoiceCorrectionRegistrationTransitionError(
            "unsupported RK registration status"
        )

    return normalized


def _normalize_reference(
    value: object | None,
) -> str | None:
    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    if not normalized:
        return None

    if len(normalized) > 500:
        raise TaxInvoiceCorrectionRegistrationError(
            "registration reference exceeds 500 characters"
        )

    return normalized


def validate_tax_invoice_correction_registration_transition(
    *,
    previous_status: str | None,
    new_status: str,
    direction: str,
) -> None:
    normalized = _normalize_status(
        new_status
    )

    if previous_status is None:
        if direction == "output":
            if normalized != "prepared":
                raise TaxInvoiceCorrectionRegistrationTransitionError(
                    "first OUTPUT RK registration event "
                    "must be prepared"
                )
            return

        if direction == "input":
            if normalized != "registered":
                raise TaxInvoiceCorrectionRegistrationTransitionError(
                    "first INPUT RK registration event "
                    "must be registered"
                )
            return

        raise TaxInvoiceCorrectionRegistrationTransitionError(
            "unsupported RK direction"
        )

    previous = _normalize_status(
        previous_status
    )

    if normalized not in RK_REGISTRATION_TRANSITIONS[
        previous
    ]:
        raise TaxInvoiceCorrectionRegistrationTransitionError(
            f"illegal RK registration transition "
            f"{previous!r} -> {normalized!r}"
        )


def validate_tax_invoice_correction_document_date(
    *,
    original_invoice_date: date,
    correction_document_date: date,
) -> None:
    if correction_document_date < original_invoice_date:
        raise TaxInvoiceCorrectionRegistrationAgeError(
            "RK document date cannot predate original PN"
        )

    age = (
        correction_document_date
        - original_invoice_date
    ).days

    if age > MAX_RK_REGISTRATION_AGE_DAYS:
        raise TaxInvoiceCorrectionRegistrationAgeError(
            "RK document date is already beyond "
            "the 1095-day registration window"
        )


def validate_tax_invoice_correction_registration_age(
    *,
    original_invoice_date: date,
    registration_date: date,
) -> None:
    if registration_date < original_invoice_date:
        raise TaxInvoiceCorrectionRegistrationAgeError(
            "RK registration cannot predate original PN"
        )

    age = (
        registration_date
        - original_invoice_date
    ).days

    if age > MAX_RK_REGISTRATION_AGE_DAYS:
        raise TaxInvoiceCorrectionRegistrationAgeError(
            "RK cannot be registered later than "
            "1095 calendar days after original PN date"
        )


async def append_tax_invoice_correction_registration_event(
    db: AsyncSession,
    *,
    company_id: int,
    tax_invoice_correction_id: int,
    status: str,
    event_date: date,
    reference: str | None,
    created_by: int,
) -> TaxInvoiceCorrectionRegistrationEvent:
    """
    Append one immutable RK registration event.

    Caller owns commit/rollback.
    """

    company_id = _positive_int(
        company_id,
        field="company_id",
    )

    tax_invoice_correction_id = _positive_int(
        tax_invoice_correction_id,
        field="tax_invoice_correction_id",
    )

    created_by = _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        event_date,
        date,
    ):
        raise TaxInvoiceCorrectionRegistrationError(
            "event_date must be a date"
        )

    normalized_status = _normalize_status(
        status
    )

    normalized_reference = _normalize_reference(
        reference
    )

    if (
        normalized_status != "prepared"
        and normalized_reference is None
    ):
        raise TaxInvoiceCorrectionRegistrationError(
            "non-prepared RK registration event "
            "requires reference"
        )

    correction = await db.scalar(
        select(
            TaxInvoiceCorrection
        )
        .where(
            TaxInvoiceCorrection.company_id
            == company_id,
            TaxInvoiceCorrection.id
            == tax_invoice_correction_id,
        )
        .with_for_update()
    )

    if correction is None:
        raise TaxInvoiceCorrectionRegistrationError(
            "RK does not belong to company or does not exist"
        )

    original_invoice = await db.scalar(
        select(
            TaxInvoice
        )
        .where(
            TaxInvoice.company_id
            == company_id,
            TaxInvoice.id
            == correction.original_tax_invoice_id,
        )
        .with_for_update()
    )

    if original_invoice is None:
        raise TaxInvoiceCorrectionRegistrationError(
            "original canonical PN is missing"
        )

    if event_date < correction.document_date:
        raise TaxInvoiceCorrectionRegistrationError(
            "RK registration event cannot predate RK document"
        )

    latest = await db.scalar(
        select(
            TaxInvoiceCorrectionRegistrationEvent
        )
        .where(
            TaxInvoiceCorrectionRegistrationEvent.company_id
            == company_id,
            TaxInvoiceCorrectionRegistrationEvent
            .tax_invoice_correction_id
            == tax_invoice_correction_id,
        )
        .order_by(
            TaxInvoiceCorrectionRegistrationEvent.event_date.desc(),
            TaxInvoiceCorrectionRegistrationEvent.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )

    if latest is not None:
        if event_date < latest.event_date:
            raise TaxInvoiceCorrectionRegistrationError(
                "RK registration history cannot be backdated"
            )

        if (
            latest.status
            == normalized_status
            and latest.event_date
            == event_date
            and latest.reference
            == normalized_reference
        ):
            return latest

    validate_tax_invoice_correction_registration_transition(
        previous_status=(
            latest.status
            if latest is not None
            else None
        ),
        new_status=normalized_status,
        direction=correction.direction,
    )

    if normalized_status == "registered":
        validate_tax_invoice_correction_registration_age(
            original_invoice_date=(
                original_invoice.document_date
            ),
            registration_date=event_date,
        )

    event = TaxInvoiceCorrectionRegistrationEvent(
        company_id=company_id,
        tax_invoice_correction_id=(
            tax_invoice_correction_id
        ),
        status=normalized_status,
        event_date=event_date,
        reference=normalized_reference,
        created_by=created_by,
    )

    db.add(
        event
    )

    await db.flush()

    return event
