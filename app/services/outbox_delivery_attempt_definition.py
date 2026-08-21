from dataclasses import dataclass
from datetime import datetime

from app.services.outbox_delivery_attempt_types import (
    OutboxDeliveryAttemptStatus,
)


@dataclass(frozen=True, slots=True)
class OutboxDeliveryAttemptDefinition:
    event_id: str
    attempt_number: int
    status: OutboxDeliveryAttemptStatus
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError(
                "event_id cannot be empty"
            )

        if self.attempt_number <= 0:
            raise ValueError(
                "attempt_number must be greater than zero"
            )

        if (
            self.status
            == OutboxDeliveryAttemptStatus.PROCESSING
        ):
            if self.finished_at is not None:
                raise ValueError(
                    "PROCESSING attempt cannot have "
                    "finished_at"
                )

            if self.error_message is not None:
                raise ValueError(
                    "PROCESSING attempt cannot have "
                    "error_message"
                )

        if (
            self.status
            == OutboxDeliveryAttemptStatus.SUCCEEDED
        ):
            if self.finished_at is None:
                raise ValueError(
                    "SUCCEEDED attempt must have "
                    "finished_at"
                )

            if self.error_message is not None:
                raise ValueError(
                    "SUCCEEDED attempt cannot have "
                    "error_message"
                )

        if (
            self.status
            == OutboxDeliveryAttemptStatus.FAILED
        ):
            if self.finished_at is None:
                raise ValueError(
                    "FAILED attempt must have "
                    "finished_at"
                )

            if (
                self.error_message is None
                or not self.error_message.strip()
            ):
                raise ValueError(
                    "FAILED attempt must have "
                    "error_message"
                )

        if (
            self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError(
                "finished_at cannot be before started_at"
            )