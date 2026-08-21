from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.outbox_delivery_attempt_repository import (
    OutboxDeliveryAttemptRepository,
)
from app.repositories.outbox_event_repository import (
    OutboxEventRepository,
)


@dataclass(
    frozen=True,
    slots=True,
)
class OutboxCompletionResult:
    """
    Immutable result of successful outbox delivery.
    """

    outbox_event_id: int
    event_id: str
    attempt_number: int
    published_at: datetime


class OutboxCompletionService:
    """
    Atomically complete successful outbox delivery.

    The caller owns the outer database transaction.
    This service does not commit or rollback it.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

        self._event_repository = (
            OutboxEventRepository(
                session=session,
            )
        )

        self._attempt_repository = (
            OutboxDeliveryAttemptRepository(
                session=session,
            )
        )

    async def complete_success(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
        worker_id: str,
    ) -> OutboxCompletionResult:
        """
        Complete one successful delivery.

        Flow:

        1. Lock the PROCESSING event owned
           by this worker.

        2. Verify the worker lease is still active.

        3. Verify attempt_number is the current
           PROCESSING attempt.

        4. Mark attempt SUCCEEDED.

        5. Mark event PUBLISHED and clear lease.

        All mutations are protected by one
        database savepoint.
        """

        if not worker_id.strip():
            raise ValueError(
                "worker_id cannot be empty"
            )

        if attempt_number <= 0:
            raise ValueError(
                "attempt_number must be positive"
            )

        async with self._session.begin_nested():
            event = (
                await self._event_repository
                .lock_processing_for_worker(
                    outbox_event_id=outbox_event_id,
                    claimed_by=worker_id,
                )
            )

            if event is None:
                raise RuntimeError(
                    "PROCESSING outbox event is not "
                    "owned by this worker"
                )

            event_id = event.event_id

            # Completion time must be captured AFTER
            # the event row lock is acquired.
            published_at = datetime.utcnow()

            lease_expires_at = event.lease_expires_at

            if lease_expires_at is None:
                raise RuntimeError(
                    "PROCESSING outbox event has "
                    "no active worker lease"
                )

            if lease_expires_at <= published_at:
                raise RuntimeError(
                    "Worker lease expired before "
                    "delivery completion"
                )

            current_attempt = (
                await self._attempt_repository
                .get_latest_processing(
                    outbox_event_id=outbox_event_id,
                )
            )

            if current_attempt is None:
                raise RuntimeError(
                    "PROCESSING outbox event has "
                    "no PROCESSING delivery attempt"
                )

            if (
                current_attempt.attempt_number
                != attempt_number
            ):
                raise RuntimeError(
                    "Attempt is not the current "
                    "PROCESSING delivery attempt"
                )

            attempt_succeeded = (
                await self._attempt_repository
                .mark_succeeded(
                    outbox_event_id=outbox_event_id,
                    attempt_number=attempt_number,
                    finished_at=published_at,
                )
            )

            if not attempt_succeeded:
                raise RuntimeError(
                    "Failed to mark outbox delivery "
                    "attempt as SUCCEEDED"
                )

            event_published = (
                await self._event_repository
                .mark_published(
                    outbox_event_id=outbox_event_id,
                    claimed_by=worker_id,
                    published_at=published_at,
                )
            )

            if not event_published:
                raise RuntimeError(
                    "Failed to mark outbox event "
                    "as PUBLISHED"
                )

            return OutboxCompletionResult(
                outbox_event_id=outbox_event_id,
                event_id=event_id,
                attempt_number=attempt_number,
                published_at=published_at,
            )
