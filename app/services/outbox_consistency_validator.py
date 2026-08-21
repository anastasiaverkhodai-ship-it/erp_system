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


class OutboxConsistencyError(Exception):
    """
    Base error for outbox lifecycle consistency.
    """


class OutboxAttemptEventMismatchError(
    OutboxConsistencyError
):
    """
    Raised when a delivery attempt belongs
    to another outbox event.
    """


class OutboxPendingAttemptsError(
    OutboxConsistencyError
):
    """
    Raised when a PENDING event already has
    delivery attempts.
    """


class OutboxLatestAttemptStatusError(
    OutboxConsistencyError
):
    """
    Raised when event status and latest delivery
    attempt status do not match.
    """


def validate_outbox_consistency(
    *,
    event: OutboxEventDefinition,
    attempts: tuple[
        OutboxDeliveryAttemptDefinition,
        ...
    ],
) -> None:
    """
    Validate lifecycle consistency between
    an outbox event and its delivery attempts.
    """

    for attempt in attempts:
        if attempt.event_id != event.event_id:
            raise OutboxAttemptEventMismatchError(
                "Outbox delivery attempt does not belong "
                "to the supplied event: "
                f"event_id='{event.event_id}', "
                f"attempt_event_id='{attempt.event_id}'"
            )

    if event.status == OutboxStatus.PENDING:
        if attempts:
            raise OutboxPendingAttemptsError(
                "PENDING outbox event cannot have "
                "delivery attempts"
            )

        return

    if not attempts:
        raise OutboxLatestAttemptStatusError(
            "Non-PENDING outbox event must have "
            "at least one delivery attempt"
        )

    latest_attempt = max(
        attempts,
        key=lambda attempt: attempt.attempt_number,
    )

    expected_attempt_status = {
        OutboxStatus.PROCESSING:
            OutboxDeliveryAttemptStatus.PROCESSING,
        OutboxStatus.PUBLISHED:
            OutboxDeliveryAttemptStatus.SUCCEEDED,
        OutboxStatus.FAILED:
            OutboxDeliveryAttemptStatus.FAILED,
    }[event.status]

    if latest_attempt.status != expected_attempt_status:
        raise OutboxLatestAttemptStatusError(
            "Outbox event status does not match "
            "latest delivery attempt: "
            f"event_status='{event.status.value}', "
            f"latest_attempt_status="
            f"'{latest_attempt.status.value}', "
            f"attempt_number="
            f"{latest_attempt.attempt_number}"
        )