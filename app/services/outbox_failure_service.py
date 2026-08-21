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
class OutboxFailureResult:
    """
    Immutable result of failed outbox delivery.
    """

    outbox_event_id: int
    event_id: str
    attempt_number: int
    failed_at: datetime
    error_message: str


class OutboxFailureService:
    """
    Atomically complete failed outbox delivery.

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

    async def complete_failure(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
        worker_id: str,
        error_message: str,
    ) -> OutboxFailureResult:
        """
        Complete one failed delivery.

        Flow:

        1. Lock the PROCESSING event owned
           by this worker.

        2. Verify the worker lease is still active.

        3. Verify attempt_number is the current
           PROCESSING attempt.

        4. Mark attempt FAILED.

        5. Mark event FAILED and clear lease.

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

        if not error_message.strip():
            raise ValueError(
                "error_message cannot be empty"
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

            # Failure completion time must be captured
            # AFTER the event row lock is acquired.
            failed_at = datetime.utcnow()

            lease_expires_at = event.lease_expires_at

            if lease_expires_at is None:
                raise RuntimeError(
                    "PROCESSING outbox event has "
                    "no active worker lease"
                )

            if lease_expires_at <= failed_at:
                raise RuntimeError(
                    "Worker lease expired before "
                    "delivery failure completion"
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

            attempt_failed = (
                await self._attempt_repository
                .mark_failed(
                    outbox_event_id=outbox_event_id,
                    attempt_number=attempt_number,
                    finished_at=failed_at,
                    error_message=error_message,
                )
            )

            if not attempt_failed:
                raise RuntimeError(
                    "Failed to mark outbox delivery "
                    "attempt as FAILED"
                )

            event_failed = (
                await self._event_repository
                .mark_failed_for_worker(
                    outbox_event_id=outbox_event_id,
                    claimed_by=worker_id,
                    failed_at=failed_at,
                )
            )

            if not event_failed:
                raise RuntimeError(
                    "Failed to mark outbox event "
                    "as FAILED"
                )

            return OutboxFailureResult(
                outbox_event_id=outbox_event_id,
                event_id=event_id,
                attempt_number=attempt_number,
                failed_at=failed_at,
                error_message=error_message,
            )
