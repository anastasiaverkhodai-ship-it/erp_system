from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_delivery_attempt import (
    OutboxDeliveryAttempt,
)
from app.services.outbox_delivery_attempt_types import (
    OutboxDeliveryAttemptStatus,
)


class OutboxDeliveryAttemptRepository:
    """
    PostgreSQL persistence operations
    for outbox delivery attempts.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

    async def try_create_processing(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
        started_at: datetime,
    ) -> bool:
        """
        Atomically try to create a PROCESSING
        delivery attempt.

        Returns True when the attempt was created.

        Returns False when an attempt with the same
        event and attempt number already exists.

        This method does not commit or rollback.
        """

        statement = (
            insert(OutboxDeliveryAttempt)
            .values(
                outbox_event_id=outbox_event_id,
                attempt_number=attempt_number,
                status=(
                    OutboxDeliveryAttemptStatus.PROCESSING
                ),
                started_at=started_at,
                finished_at=None,
                error_message=None,
                created_at=datetime.utcnow(),
            )
            .on_conflict_do_nothing(
                constraint=(
                    "uq_outbox_delivery_attempt_event_number"
                )
            )
            .returning(
                OutboxDeliveryAttempt.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        inserted_id = result.scalar_one_or_none()

        return inserted_id is not None

    async def get_by_number(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
    ) -> OutboxDeliveryAttempt | None:
        """
        Return one delivery attempt
        by event and attempt number.
        """

        statement = select(
            OutboxDeliveryAttempt
        ).where(
            OutboxDeliveryAttempt.outbox_event_id
            == outbox_event_id,
            OutboxDeliveryAttempt.attempt_number
            == attempt_number,
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def list_for_event(
        self,
        *,
        outbox_event_id: int,
    ) -> tuple[OutboxDeliveryAttempt, ...]:
        """
        Return all delivery attempts for one event,
        ordered by attempt number.
        """

        statement = (
            select(
                OutboxDeliveryAttempt
            )
            .where(
                OutboxDeliveryAttempt.outbox_event_id
                == outbox_event_id
            )
            .order_by(
                OutboxDeliveryAttempt.attempt_number
            )
        )

        result = await self._session.scalars(
            statement,
        )

        return tuple(
            result.all()
        )

    async def mark_succeeded(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
        finished_at: datetime,
    ) -> bool:
        """
        Atomically transition one delivery attempt
        from PROCESSING to SUCCEEDED.

        Returns True only when the transition
        actually happened.
        """

        statement = (
            update(OutboxDeliveryAttempt)
            .where(
                OutboxDeliveryAttempt.outbox_event_id
                == outbox_event_id,
                OutboxDeliveryAttempt.attempt_number
                == attempt_number,
                OutboxDeliveryAttempt.status
                == OutboxDeliveryAttemptStatus.PROCESSING,
                OutboxDeliveryAttempt.started_at
                <= finished_at,
            )
            .values(
                status=(
                    OutboxDeliveryAttemptStatus.SUCCEEDED
                ),
                finished_at=finished_at,
                error_message=None,
            )
            .returning(
                OutboxDeliveryAttempt.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None

    async def mark_failed(
        self,
        *,
        outbox_event_id: int,
        attempt_number: int,
        finished_at: datetime,
        error_message: str,
    ) -> bool:
        """
        Atomically transition one delivery attempt
        from PROCESSING to FAILED.

        Returns True only when the transition
        actually happened.
        """

        if not error_message.strip():
            raise ValueError(
                "error_message cannot be empty"
            )

        statement = (
            update(OutboxDeliveryAttempt)
            .where(
                OutboxDeliveryAttempt.outbox_event_id
                == outbox_event_id,
                OutboxDeliveryAttempt.attempt_number
                == attempt_number,
                OutboxDeliveryAttempt.status
                == OutboxDeliveryAttemptStatus.PROCESSING,
                OutboxDeliveryAttempt.started_at
                <= finished_at,
            )
            .values(
                status=(
                    OutboxDeliveryAttemptStatus.FAILED
                ),
                finished_at=finished_at,
                error_message=error_message,
            )
            .returning(
                OutboxDeliveryAttempt.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None

    async def get_next_attempt_number(
        self,
        *,
        outbox_event_id: int,
    ) -> int:
        """
        Return the next delivery attempt number
        for one outbox event.

        Example:

        no attempts -> 1
        attempts 1, 2 -> 3

        Concurrency safety is provided by the caller
        holding the corresponding outbox event row lock.
        """

        statement = select(
            func.coalesce(
                func.max(
                    OutboxDeliveryAttempt.attempt_number
                ),
                0,
            )
            + 1
        ).where(
            OutboxDeliveryAttempt.outbox_event_id
            == outbox_event_id
        )

        result = await self._session.execute(
            statement,
        )

        next_attempt_number = result.scalar_one()

        return int(next_attempt_number)
    async def get_latest_processing(
        self,
        *,
        outbox_event_id: int,
    ) -> OutboxDeliveryAttempt | None:
        """
        Return the latest PROCESSING delivery attempt
        for one outbox event.

        Returns None when no PROCESSING attempt exists.

        The caller is responsible for holding the
        corresponding outbox event row lock when this
        method is used during recovery.
        """

        statement = (
            select(
                OutboxDeliveryAttempt
            )
            .where(
                OutboxDeliveryAttempt.outbox_event_id
                == outbox_event_id,
                OutboxDeliveryAttempt.status
                == OutboxDeliveryAttemptStatus.PROCESSING,
            )
            .order_by(
                OutboxDeliveryAttempt.attempt_number.desc()
            )
            .limit(1)
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()
