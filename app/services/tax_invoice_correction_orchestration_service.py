from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_invoice_correction import (
    TaxInvoiceCorrection,
)
from app.models.tax_invoice_correction_registration_event import (
    TaxInvoiceCorrectionRegistrationEvent,
)
from app.services.idempotency_completion_service import (
    complete_idempotent_operation,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.idempotency_request_validator import (
    IdempotencyKeyReuseError,
)
from app.services.idempotency_reservation_service import (
    reserve_idempotent_request,
)
from app.services.tax_invoice_correction_persistence_service import (
    create_tax_invoice_correction,
)
from app.services.tax_invoice_correction_registration_lifecycle_service import (
    append_tax_invoice_correction_registration_event,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    TaxInvoiceCorrectionLineSource,
    normalize_correction_line_sources,
)


RK_OUTPUT_CREATE_OPERATION = (
    "tax_invoice_correction_create_output"
)

RK_INPUT_CREATE_OPERATION = (
    "tax_invoice_correction_create_input"
)

RK_REGISTRATION_APPEND_OPERATION = (
    "tax_invoice_correction_registration_append"
)

RK_RESULT_TYPE = "tax_invoice_correction"

RK_REGISTRATION_RESULT_TYPE = (
    "tax_invoice_correction_registration_event"
)


class TaxInvoiceCorrectionOrchestrationError(Exception):
    """Base RK idempotent orchestration error."""


class TaxInvoiceCorrectionIdempotencyConflictError(
    TaxInvoiceCorrectionOrchestrationError
):
    """Same request key was used for different semantic input."""


class TaxInvoiceCorrectionInProgressError(
    TaxInvoiceCorrectionOrchestrationError
):
    """Equivalent RK request is already in progress."""


class TaxInvoiceCorrectionFailedError(
    TaxInvoiceCorrectionOrchestrationError
):
    """Stored idempotency state cannot start/replay the request."""


class TaxInvoiceCorrectionStoredResultError(
    TaxInvoiceCorrectionOrchestrationError
):
    """Stored reusable idempotency result is invalid."""


def _request_key(
    value: object,
) -> str:
    if value is None:
        raise TaxInvoiceCorrectionOrchestrationError(
            "request_key is required"
        )

    normalized = str(
        value
    ).strip()

    if not normalized:
        raise TaxInvoiceCorrectionOrchestrationError(
            "request_key is required"
        )

    if len(normalized) > 255:
        raise TaxInvoiceCorrectionOrchestrationError(
            "request_key exceeds 255 characters"
        )

    return normalized


def _required_text(
    value: object,
    *,
    field: str,
    max_length: int,
) -> str:
    if value is None:
        raise TaxInvoiceCorrectionOrchestrationError(
            f"{field} is required"
        )

    normalized = str(
        value
    ).strip()

    if not normalized:
        raise TaxInvoiceCorrectionOrchestrationError(
            f"{field} is required"
        )

    if len(normalized) > max_length:
        raise TaxInvoiceCorrectionOrchestrationError(
            f"{field} exceeds maximum length"
        )

    return normalized


def _canonical_create_payload(
    *,
    direction: str,
    original_tax_invoice_id: int,
    document_number: str,
    document_date: date,
    sources: tuple[
        TaxInvoiceCorrectionLineSource,
        ...,
    ],
    registration_date: date | None,
    registration_reference: str | None,
) -> dict:
    return {
        "direction": direction,
        "original_tax_invoice_id": (
            original_tax_invoice_id
        ),
        "document_number": (
            document_number
        ),
        "document_date": (
            document_date.isoformat()
        ),
        "registration_date": (
            registration_date.isoformat()
            if registration_date is not None
            else None
        ),
        "registration_reference": (
            registration_reference
        ),
        "lines": [
            {
                **({"replacement":source.replacement} if source.replacement else {}),
                "line_number": (
                    source.line_number
                ),
                "source_kind": (
                    source.source_kind
                ),
                "source_id": (
                    source.source_id
                ),
                "reason_code": (
                    source.reason_code
                ),
                "tax_credit_evidence_id": (
                    source.tax_credit_evidence_id
                ),
            }
            for source in sources
        ],
    }


def _canonical_registration_payload(
    *,
    tax_invoice_correction_id: int,
    status: str,
    event_date: date,
    reference: str,
) -> dict:
    return {
        "tax_invoice_correction_id": (
            tax_invoice_correction_id
        ),
        "status": status,
        "event_date": event_date.isoformat(),
        "reference": reference,
    }


async def _reusable_correction(
    db: AsyncSession,
    *,
    company_id: int,
    result_id: str,
    expected_direction: str,
) -> TaxInvoiceCorrection:
    try:
        correction_id = int(
            result_id
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise TaxInvoiceCorrectionStoredResultError(
            "stored RK result ID is invalid"
        ) from exc

    correction = await db.scalar(
        select(
            TaxInvoiceCorrection
        )
        .where(
            TaxInvoiceCorrection.company_id
            == company_id,
            TaxInvoiceCorrection.id
            == correction_id,
        )
        .with_for_update()
    )

    if (
        correction is None
        or correction.direction
        != expected_direction
    ):
        raise TaxInvoiceCorrectionStoredResultError(
            "stored RK result is missing "
            "or has wrong direction"
        )

    return correction


async def _reusable_registration_event(
    db: AsyncSession,
    *,
    company_id: int,
    tax_invoice_correction_id: int,
    result_id: str,
) -> TaxInvoiceCorrectionRegistrationEvent:
    try:
        event_id = int(
            result_id
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise TaxInvoiceCorrectionStoredResultError(
            "stored RK registration result ID is invalid"
        ) from exc

    event = await db.scalar(
        select(
            TaxInvoiceCorrectionRegistrationEvent
        )
        .where(
            TaxInvoiceCorrectionRegistrationEvent.company_id
            == company_id,
            TaxInvoiceCorrectionRegistrationEvent.id
            == event_id,
            TaxInvoiceCorrectionRegistrationEvent
            .tax_invoice_correction_id
            == tax_invoice_correction_id,
        )
        .with_for_update()
    )

    if event is None:
        raise TaxInvoiceCorrectionStoredResultError(
            "stored RK registration result is missing"
        )

    return event


def _validate_execution_for_new_write(
    decision: IdempotencyDecision,
) -> None:
    if (
        decision
        == IdempotencyDecision.ALREADY_IN_PROGRESS
    ):
        raise TaxInvoiceCorrectionInProgressError(
            "RK idempotent operation is already in progress"
        )

    if (
        decision
        != IdempotencyDecision.START_NEW
    ):
        raise TaxInvoiceCorrectionFailedError(
            "RK idempotent operation cannot start: "
            f"{decision.value}"
        )


async def create_tax_invoice_correction_idempotent(
    db: AsyncSession,
    *,
    company_id: int,
    request_key: str,
    direction: str,
    original_tax_invoice_id: int,
    document_number: str,
    document_date: date,
    sources: list[
        TaxInvoiceCorrectionLineSource
    ],
    created_by: int,
    registration_date: date | None = None,
    registration_reference: str | None = None,
    received_on: date | None = None,
) -> TaxInvoiceCorrection:
    """
    Reserve request-key, persist/reuse canonical RK, append its initial
    legal registration event, and store a reusable idempotency result.

    Everything is part of the same caller-owned transaction.
    """

    key = _request_key(
        request_key
    )

    number = _required_text(
        document_number,
        field="document_number",
        max_length=120,
    )

    if direction not in {
        "input",
        "output",
    }:
        raise TaxInvoiceCorrectionOrchestrationError(
            "direction must be input or output"
        )

    if not isinstance(
        document_date,
        date,
    ):
        raise TaxInvoiceCorrectionOrchestrationError(
            "document_date must be a date"
        )

    normalized_sources = (
        normalize_correction_line_sources(
            sources
        )
    )

    if direction == "output":
        if (
            registration_date is not None
            or registration_reference is not None
        ):
            raise TaxInvoiceCorrectionOrchestrationError(
                "OUTPUT RK creation must not supply "
                "initial registration receipt data"
            )

        operation = (
            RK_OUTPUT_CREATE_OPERATION
        )

    else:
        if not isinstance(
            registration_date,
            date,
        ):
            raise TaxInvoiceCorrectionOrchestrationError(
                "INPUT RK requires registration_date"
            )

        registration_reference = _required_text(
            registration_reference,
            field="registration_reference",
            max_length=500,
        )

        operation = (
            RK_INPUT_CREATE_OPERATION
        )

    request_payload = (
        _canonical_create_payload(
            direction=direction,
            original_tax_invoice_id=(
                original_tax_invoice_id
            ),
            document_number=number,
            document_date=document_date,
            sources=normalized_sources,
            registration_date=(
                registration_date
            ),
            registration_reference=(
                registration_reference
            ),
        )
    )

    if received_on is not None:
        request_payload['received_on'] = received_on.isoformat()

    request_fingerprint = (
        generate_request_fingerprint(
            request_payload
        )
    )

    try:
        execution = await reserve_idempotent_request(
            session=db,
            company_id=company_id,
            operation=operation,
            idempotency_key=key,
            request_payload=request_payload,
        )
    except IdempotencyKeyReuseError as exc:
        raise TaxInvoiceCorrectionIdempotencyConflictError(
            str(exc)
        ) from exc

    if (
        execution.decision
        == IdempotencyDecision.REUSE_RESULT
    ):
        result = execution.reusable_result

        if (
            result is None
            or result.result_type
            != RK_RESULT_TYPE
        ):
            raise TaxInvoiceCorrectionStoredResultError(
                "stored RK idempotency result is invalid"
            )

        return await _reusable_correction(
            db,
            company_id=company_id,
            result_id=result.result_id,
            expected_direction=direction,
        )

    _validate_execution_for_new_write(
        execution.decision
    )

    correction = await create_tax_invoice_correction(
        db,
        company_id=company_id,
        original_tax_invoice_id=(
            original_tax_invoice_id
        ),
        document_number=number,
        document_date=document_date,
        sources=list(
            normalized_sources
        ),
        created_by=created_by,
    )

    if correction.direction != direction:
        raise TaxInvoiceCorrectionOrchestrationError(
            "original PN direction does not match RK endpoint"
        )

    if direction == "output":
        await append_tax_invoice_correction_registration_event(
            db,
            company_id=company_id,
            tax_invoice_correction_id=(
                correction.id
            ),
            status="prepared",
            event_date=document_date,
            reference=None,
            created_by=created_by,
        )

    else:
        await append_tax_invoice_correction_registration_event(
            db,
            company_id=company_id,
            tax_invoice_correction_id=(
                correction.id
            ),
            status="registered",
            received_on=received_on,
            event_date=registration_date,
            reference=registration_reference,
            created_by=created_by,
        )

    if direction == 'output':
        from app.services.tax_invoice_correction_accounting_service import post_output_correction
        await post_output_correction(db, company_id=company_id, correction_id=correction.id, created_by=created_by)

    await complete_idempotent_operation(
        session=db,
        company_id=company_id,
        operation=operation,
        idempotency_key=key,
        request_fingerprint=(
            request_fingerprint
        ),
        result_type=RK_RESULT_TYPE,
        result_id=str(
            correction.id
        ),
        result_payload=None,
    )

    return correction


async def append_tax_invoice_correction_registration_idempotent(
    db: AsyncSession,
    *,
    company_id: int,
    request_key: str,
    tax_invoice_correction_id: int,
    status: str,
    event_date: date,
    reference: str,
    created_by: int,
    registration_party: str | None = None,
    received_on: date | None = None,
) -> TaxInvoiceCorrectionRegistrationEvent:
    """
    Generic request-key idempotency around an append-only RK
    registration event.

    Caller owns commit/rollback.
    """

    key = _request_key(
        request_key
    )

    normalized_status = _required_text(
        status,
        field="status",
        max_length=20,
    ).lower()

    normalized_reference = _required_text(
        reference,
        field="reference",
        max_length=500,
    )

    if not isinstance(
        event_date,
        date,
    ):
        raise TaxInvoiceCorrectionOrchestrationError(
            "event_date must be a date"
        )

    request_payload = (
        _canonical_registration_payload(
            tax_invoice_correction_id=(
                tax_invoice_correction_id
            ),
            status=normalized_status,
            event_date=event_date,
            reference=normalized_reference,
        )
    )

    if registration_party is not None:
        request_payload['registration_party'] = registration_party
    if received_on is not None:
        request_payload['received_on'] = received_on.isoformat()

    request_fingerprint = (
        generate_request_fingerprint(
            request_payload
        )
    )

    try:
        execution = await reserve_idempotent_request(
            session=db,
            company_id=company_id,
            operation=(
                RK_REGISTRATION_APPEND_OPERATION
            ),
            idempotency_key=key,
            request_payload=request_payload,
        )
    except IdempotencyKeyReuseError as exc:
        raise TaxInvoiceCorrectionIdempotencyConflictError(
            str(exc)
        ) from exc

    if (
        execution.decision
        == IdempotencyDecision.REUSE_RESULT
    ):
        result = execution.reusable_result

        if (
            result is None
            or result.result_type
            != RK_REGISTRATION_RESULT_TYPE
        ):
            raise TaxInvoiceCorrectionStoredResultError(
                "stored RK registration idempotency "
                "result is invalid"
            )

        return await _reusable_registration_event(
            db,
            company_id=company_id,
            tax_invoice_correction_id=(
                tax_invoice_correction_id
            ),
            result_id=result.result_id,
        )

    _validate_execution_for_new_write(
        execution.decision
    )

    event = await append_tax_invoice_correction_registration_event(
        db,
        company_id=company_id,
        tax_invoice_correction_id=(
            tax_invoice_correction_id
        ),
        status=normalized_status,
        event_date=event_date,
        reference=normalized_reference,
        created_by=created_by,
        registration_party=registration_party,
        received_on=received_on,
    )

    await complete_idempotent_operation(
        session=db,
        company_id=company_id,
        operation=(
            RK_REGISTRATION_APPEND_OPERATION
        ),
        idempotency_key=key,
        request_fingerprint=(
            request_fingerprint
        ),
        result_type=(
            RK_REGISTRATION_RESULT_TYPE
        ),
        result_id=str(
            event.id
        ),
        result_payload=None,
    )

    return event
