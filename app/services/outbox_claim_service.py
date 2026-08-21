from dataclasses import dataclass
from datetime import datetime, timedelta

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
class OutboxClaim:
    """
    Immutable result of successfully claiming
    one outbox event for delivery.
    """

    outbox_event_id: int
    event_id: str
    company_id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: str
    attempt_number: int
    claimed_by: str
    started_at: datetime
    lease_expires_at: datetime


class OutboxClaimService:
    """
    Atomically claim the next available outbox event.

    The caller owns the outer database transaction.
    This service does not commit or rollback it.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        lease_duration: timedelta = timedelta(
            minutes=5,
        ),
    ) -> None:
        if not worker_id.strip():
            raise ValueError(
                "worker_id cannot be empty"
            )

        if lease_duration <= timedelta(0):
            raise ValueError(
                "lease_duration must be positive"
            )

        self._session = session
        self._worker_id = worker_id
        self._lease_duration = lease_duration

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

    async def claim_next(
        self,
    ) -> OutboxClaim | None:
        """
        Claim the next available event.

        Flow:

        1. Lock PENDING/FAILED event with
           FOR UPDATE SKIP LOCKED.

        2. Calculate next attempt number.

        3. Transition event to PROCESSING
           and assign a worker lease.

        4. Create matching PROCESSING attempt.

        All claim mutations are protected by
        one database savepoint.

        Returns None when no event is available.
        """

        async with self._session.begin_nested():
            event = (
                await self._event_repository
                .lock_next_available()
            )

            if event is None:
                return None

            outbox_event_id = event.id
            event_id = event.event_id
            company_id = event.company_id
            event_type = event.event_type
            aggregate_type = event.aggregate_type
            aggregate_id = event.aggregate_id
            payload = event.payload

            attempt_number = (
                await self._attempt_repository
                .get_next_attempt_number(
                    outbox_event_id=outbox_event_id,
                )
            )

            started_at = datetime.utcnow()

            lease_expires_at = (
                started_at
                + self._lease_duration
            )

            marked_processing = (
                await self._event_repository
                .mark_processing(
                    outbox_event_id=outbox_event_id,
                    claimed_by=self._worker_id,
                    processing_started_at=started_at,
                    lease_expires_at=lease_expires_at,
                )
            )

            if not marked_processing:
                raise RuntimeError(
                    "Failed to transition claimed "
                    "outbox event to PROCESSING"
                )

            attempt_created = (
                await self._attempt_repository
                .try_create_processing(
                    outbox_event_id=outbox_event_id,
                    attempt_number=attempt_number,
                    started_at=started_at,
                )
            )

            if not attempt_created:
                raise RuntimeError(
                    "Failed to create PROCESSING "
                    "outbox delivery attempt"
                )

            return OutboxClaim(
                outbox_event_id=outbox_event_id,
                event_id=event_id,
                company_id=company_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                attempt_number=attempt_number,
                claimed_by=self._worker_id,
                started_at=started_at,
                lease_expires_at=lease_expires_at,
            )