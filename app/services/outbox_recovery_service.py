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
class OutboxRecoveryResult:
    """
    Immutable result of successfully recovering
    one expired outbox event.
    """

    outbox_event_id: int
    event_id: str
    attempt_number: int
    failed_at: datetime
    error_message: str


class OutboxRecoveryService:
    """
    Recover abandoned PROCESSING outbox events
    whose worker lease has expired.

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

    async def recover_next_expired(
        self,
        *,
        now: datetime,
    ) -> OutboxRecoveryResult | None:
        """
        Recover the next expired PROCESSING event.

        Flow:

        1. Lock one expired PROCESSING event
           with FOR UPDATE SKIP LOCKED.

        2. Find its current PROCESSING attempt.

        3. Mark the attempt FAILED.

        4. Mark the event FAILED and clear
           its worker lease.

        All mutations are protected by one
        database savepoint.

        Returns None when no expired event exists.
        """

        async with self._session.begin_nested():
            event = (
                await self._event_repository
                .lock_next_expired_processing(
                    now=now,
                )
            )

            if event is None:
                return None

            outbox_event_id = event.id
            event_id = event.event_id

            attempt = (
                await self._attempt_repository
                .get_latest_processing(
                    outbox_event_id=outbox_event_id,
                )
            )

            if attempt is None:
                raise RuntimeError(
                    "Expired PROCESSING outbox event "
                    "has no PROCESSING delivery attempt"
                )

            attempt_number = attempt.attempt_number

            error_message = (
                "Worker lease expired before delivery "
                "was completed"
            )

            attempt_failed = (
                await self._attempt_repository.mark_failed(
                    outbox_event_id=outbox_event_id,
                    attempt_number=attempt_number,
                    finished_at=now,
                    error_message=error_message,
                )
            )

            if not attempt_failed:
                raise RuntimeError(
                    "Failed to mark expired outbox "
                    "delivery attempt as FAILED"
                )

            event_failed = (
                await self._event_repository
                .mark_expired_processing_failed(
                    outbox_event_id=outbox_event_id,
                    now=now,
                )
            )

            if not event_failed:
                raise RuntimeError(
                    "Failed to mark expired outbox "
                    "event as FAILED"
                )

            return OutboxRecoveryResult(
                outbox_event_id=outbox_event_id,
                event_id=event_id,
                attempt_number=attempt_number,
                failed_at=now,
                error_message=error_message,
            )
