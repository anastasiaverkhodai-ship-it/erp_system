from dataclasses import replace
from datetime import datetime

from app.services.outbox_consistency_validator import (
    validate_outbox_consistency,
)
from app.services.outbox_delivery_attempt_definition import (
    OutboxDeliveryAttemptDefinition,
)
from app.services.outbox_delivery_attempt_types import (
    OutboxDeliveryAttemptStatus,
)
from app.services.outbox_event_definition import (
    OutboxEventDefinition,
)
from app.services.outbox_types import (
    OutboxStatus,
)


class OutboxTransitionError(Exception):
    """
    Base error for outbox lifecycle transitions.
    """


class OutboxStartProcessingError(
    OutboxTransitionError
):
    """
    Raised when an event cannot start
    a new delivery attempt.
    """


class OutboxFinishProcessingError(
    OutboxTransitionError
):
    """
    Raised when a PROCESSING event cannot
    finish its current delivery attempt.
    """


def start_outbox_processing(
    *,
    event: OutboxEventDefinition,
    attempts: tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ],
    started_at: datetime,
) -> tuple[
    OutboxEventDefinition,
    OutboxDeliveryAttemptDefinition,
]:
    """
    Start a new delivery attempt.

    Allowed:

    PENDING -> PROCESSING
    FAILED  -> PROCESSING

    A FAILED event starts the next numbered attempt.
    """

    validate_outbox_consistency(
        event=event,
        attempts=attempts,
    )

    if event.status not in (
        OutboxStatus.PENDING,
        OutboxStatus.FAILED,
    ):
        raise OutboxStartProcessingError(
            "Outbox event cannot start processing "
            f"from status '{event.status.value}'"
        )

    next_attempt_number = (
        max(
            (
                attempt.attempt_number
                for attempt in attempts
            ),
            default=0,
        )
        + 1
    )

    processing_event = replace(
        event,
        status=OutboxStatus.PROCESSING,
    )

    processing_attempt = (
        OutboxDeliveryAttemptDefinition(
            event_id=event.event_id,
            attempt_number=next_attempt_number,
            status=(
                OutboxDeliveryAttemptStatus.PROCESSING
            ),
            started_at=started_at,
        )
    )

    validate_outbox_consistency(
        event=processing_event,
        attempts=(
            *attempts,
            processing_attempt,
        ),
    )

    return (
        processing_event,
        processing_attempt,
    )


def publish_outbox_event(
    *,
    event: OutboxEventDefinition,
    attempts: tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ],
    finished_at: datetime,
) -> tuple[
    OutboxEventDefinition,
    OutboxDeliveryAttemptDefinition,
]:
    """
    Finish the current delivery attempt successfully.

    PROCESSING -> PUBLISHED
    """

    validate_outbox_consistency(
        event=event,
        attempts=attempts,
    )

    if event.status != OutboxStatus.PROCESSING:
        raise OutboxFinishProcessingError(
            "Only a PROCESSING outbox event "
            "can be published"
        )

    latest_attempt = max(
        attempts,
        key=lambda attempt: attempt.attempt_number,
    )

    if (
        latest_attempt.status
        != OutboxDeliveryAttemptStatus.PROCESSING
    ):
        raise OutboxFinishProcessingError(
            "Latest outbox delivery attempt "
            "must be PROCESSING"
        )

    published_attempt = replace(
        latest_attempt,
        status=OutboxDeliveryAttemptStatus.SUCCEEDED,
        finished_at=finished_at,
    )

    published_event = replace(
        event,
        status=OutboxStatus.PUBLISHED,
    )

    previous_attempts = tuple(
        attempt
        for attempt in attempts
        if attempt.attempt_number
        != latest_attempt.attempt_number
    )

    validate_outbox_consistency(
        event=published_event,
        attempts=(
            *previous_attempts,
            published_attempt,
        ),
    )

    return (
        published_event,
        published_attempt,
    )


def fail_outbox_event(
    *,
    event: OutboxEventDefinition,
    attempts: tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ],
    finished_at: datetime,
    error_message: str,
) -> tuple[
    OutboxEventDefinition,
    OutboxDeliveryAttemptDefinition,
]:
    """
    Finish the current delivery attempt with failure.

    PROCESSING -> FAILED
    """

    validate_outbox_consistency(
        event=event,
        attempts=attempts,
    )

    if event.status != OutboxStatus.PROCESSING:
        raise OutboxFinishProcessingError(
            "Only a PROCESSING outbox event "
            "can fail"
        )

    latest_attempt = max(
        attempts,
        key=lambda attempt: attempt.attempt_number,
    )

    if (
        latest_attempt.status
        != OutboxDeliveryAttemptStatus.PROCESSING
    ):
        raise OutboxFinishProcessingError(
            "Latest outbox delivery attempt "
            "must be PROCESSING"
        )

    failed_attempt = replace(
        latest_attempt,
        status=OutboxDeliveryAttemptStatus.FAILED,
        finished_at=finished_at,
        error_message=error_message,
    )

    failed_event = replace(
        event,
        status=OutboxStatus.FAILED,
    )

    previous_attempts = tuple(
        attempt
        for attempt in attempts
        if attempt.attempt_number
        != latest_attempt.attempt_number
    )

    validate_outbox_consistency(
        event=failed_event,
        attempts=(
            *previous_attempts,
            failed_attempt,
        ),
    )

    return (
        failed_event,
        failed_attempt,
    )