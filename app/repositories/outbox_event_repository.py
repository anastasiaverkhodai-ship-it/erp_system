from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent
from app.services.outbox_event_definition import (
    OutboxEventDefinition,
)
from app.services.outbox_types import OutboxStatus


class OutboxEventRepository:
    """
    PostgreSQL persistence operations
    for outbox events.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

    async def try_create(
        self,
        *,
        event: OutboxEventDefinition,
    ) -> bool:
        """
        Atomically try to persist a new PENDING
        outbox event.

        Returns True when the event was created.
        Returns False when event_id already exists.

        This method does not commit or rollback.
        """

        if event.status != OutboxStatus.PENDING:
            raise ValueError(
                "New outbox event must have "
                "PENDING status"
            )

        now = datetime.utcnow()

        statement = (
            insert(OutboxEvent)
            .values(
                event_id=event.event_id,
                company_id=event.company_id,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
                status=event.status,
                occurred_at=event.occurred_at,
                created_at=now,
                updated_at=now,
                published_at=None,
                claimed_by=None,
                processing_started_at=None,
                lease_expires_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    OutboxEvent.event_id,
                ],
            )
            .returning(
                OutboxEvent.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        inserted_id = result.scalar_one_or_none()

        return inserted_id is not None

    async def get_by_event_id(
        self,
        *,
        event_id: str,
    ) -> OutboxEvent | None:
        """
        Return an outbox event by its stable event_id.
        """

        statement = select(
            OutboxEvent
        ).where(
            OutboxEvent.event_id == event_id
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def lock_next_available(
        self,
    ) -> OutboxEvent | None:
        """
        Lock and return the next outbox event
        available for delivery.

        Eligible statuses:

        PENDING
        FAILED

        FOR UPDATE SKIP LOCKED prevents workers
        from claiming the same row concurrently.

        This method does not commit or rollback.
        """

        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_(
                    (
                        OutboxStatus.PENDING,
                        OutboxStatus.FAILED,
                    )
                )
            )
            .order_by(
                OutboxEvent.occurred_at,
                OutboxEvent.id,
            )
            .with_for_update(
                skip_locked=True,
            )
            .limit(1)
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def mark_processing(
        self,
        *,
        outbox_event_id: int,
        claimed_by: str,
        processing_started_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """
        Atomically transition an available outbox event
        to PROCESSING and assign a worker lease.

        Allowed transitions:

        PENDING -> PROCESSING
        FAILED  -> PROCESSING

        This method does not commit or rollback.
        """

        if not claimed_by.strip():
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if lease_expires_at <= processing_started_at:
            raise ValueError(
                "lease_expires_at must be later than "
                "processing_started_at"
            )

        statement = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == outbox_event_id,
                OutboxEvent.status.in_(
                    (
                        OutboxStatus.PENDING,
                        OutboxStatus.FAILED,
                    )
                ),
            )
            .values(
                status=OutboxStatus.PROCESSING,
                claimed_by=claimed_by,
                processing_started_at=processing_started_at,
                lease_expires_at=lease_expires_at,
                updated_at=processing_started_at,
                published_at=None,
            )
            .returning(
                OutboxEvent.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None

    async def lock_next_expired_processing(
        self,
        *,
        now: datetime,
    ) -> OutboxEvent | None:
        """
        Lock and return the next PROCESSING event
        whose worker lease has expired.

        FOR UPDATE SKIP LOCKED prevents multiple
        recovery workers from recovering the same
        event concurrently.

        This method does not change the event and
        does not commit or rollback.
        """

        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status
                == OutboxStatus.PROCESSING,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at <= now,
            )
            .order_by(
                OutboxEvent.lease_expires_at,
                OutboxEvent.id,
            )
            .with_for_update(
                skip_locked=True,
            )
            .limit(1)
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def mark_expired_processing_failed(
        self,
        *,
        outbox_event_id: int,
        now: datetime,
    ) -> bool:
        """
        Recover an abandoned PROCESSING event whose
        worker lease has expired.

        Transition:

        PROCESSING + expired lease -> FAILED

        Worker lease fields are cleared so the event
        can later be claimed again.

        Returns True only when the transition
        actually happened.

        This method does not commit or rollback.
        """

        statement = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == outbox_event_id,
                OutboxEvent.status
                == OutboxStatus.PROCESSING,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at <= now,
            )
            .values(
                status=OutboxStatus.FAILED,
                claimed_by=None,
                processing_started_at=None,
                lease_expires_at=None,
                updated_at=now,
                published_at=None,
            )
            .returning(
                OutboxEvent.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None

    async def lock_processing_for_worker(
        self,
        *,
        outbox_event_id: int,
        claimed_by: str,
    ) -> OutboxEvent | None:
        """
        Lock one PROCESSING outbox event owned
        by the specified worker.

        Returns None when:

        - the event does not exist;
        - it is not PROCESSING;
        - it belongs to another worker.

        FOR UPDATE serializes completion/recovery
        operations for the same event.

        This method does not commit or rollback.
        """

        if not claimed_by.strip():
            raise ValueError(
                "claimed_by cannot be empty"
            )

        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.id == outbox_event_id,
                OutboxEvent.status
                == OutboxStatus.PROCESSING,
                OutboxEvent.claimed_by == claimed_by,
            )
            .with_for_update()
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def mark_published(
        self,
        *,
        outbox_event_id: int,
        claimed_by: str,
        published_at: datetime,
    ) -> bool:
        """
        Atomically complete successful delivery.

        Transition:

        PROCESSING -> PUBLISHED

        The event must still belong to the specified
        worker.

        Worker lease fields are cleared after
        successful completion.

        Returns True only when the transition
        actually happened.

        This method does not commit or rollback.
        """

        if not claimed_by.strip():
            raise ValueError(
                "claimed_by cannot be empty"
            )

        statement = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == outbox_event_id,
                OutboxEvent.status
                == OutboxStatus.PROCESSING,
                OutboxEvent.claimed_by == claimed_by,
                OutboxEvent.processing_started_at.is_not(None),
                OutboxEvent.processing_started_at
                <= published_at,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at
                > published_at,
            )
            .values(
                status=OutboxStatus.PUBLISHED,
                claimed_by=None,
                processing_started_at=None,
                lease_expires_at=None,
                published_at=published_at,
                updated_at=published_at,
            )
            .returning(
                OutboxEvent.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None

    async def mark_failed_for_worker(
        self,
        *,
        outbox_event_id: int,
        claimed_by: str,
        failed_at: datetime,
    ) -> bool:
        """
        Atomically complete a failed delivery.

        Transition:

        PROCESSING -> FAILED

        The event must still belong to the specified
        worker.

        Worker lease fields are cleared so the event
        can later be claimed again for retry.

        Returns True only when the transition
        actually happened.

        This method does not commit or rollback.
        """

        if not claimed_by.strip():
            raise ValueError(
                "claimed_by cannot be empty"
            )

        statement = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == outbox_event_id,
                OutboxEvent.status
                == OutboxStatus.PROCESSING,
                OutboxEvent.claimed_by == claimed_by,
                OutboxEvent.processing_started_at.is_not(None),
                OutboxEvent.processing_started_at
                <= failed_at,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at
                > failed_at,
            )
            .values(
                status=OutboxStatus.FAILED,
                claimed_by=None,
                processing_started_at=None,
                lease_expires_at=None,
                published_at=None,
                updated_at=failed_at,
            )
            .returning(
                OutboxEvent.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None