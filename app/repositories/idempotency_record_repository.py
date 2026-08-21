from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_record import IdempotencyRecord
from app.services.idempotency_types import IdempotencyStatus


class IdempotencyRecordRepository:
    """
    PostgreSQL persistence operations for idempotency records.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

    async def try_reserve(
        self,
        *,
        company_id: int,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> bool:
        """
        Atomically try to reserve an idempotency key.

        Returns:
            True:
                this transaction successfully created
                the reservation.

            False:
                the reservation already exists.

        This method does not commit or rollback.
        Transaction ownership stays with the caller.
        """

        now = datetime.utcnow()

        statement = (
            insert(IdempotencyRecord)
            .values(
                company_id=company_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                status=IdempotencyStatus.IN_PROGRESS,
                created_at=now,
                updated_at=now,
                finished_at=None,
            )
            .on_conflict_do_nothing(
                constraint=(
                    "uq_idempotency_record_company_operation_key"
                ),
            )
            .returning(
                IdempotencyRecord.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        inserted_id = result.scalar_one_or_none()

        return inserted_id is not None

    async def get_by_key(
        self,
        *,
        company_id: int,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        """
        Return an existing idempotency record
        for the exact business key.

        Returns None when no record exists.
        """

        statement = select(
            IdempotencyRecord
        ).where(
            IdempotencyRecord.company_id == company_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def mark_completed(
        self,
        *,
        company_id: int,
        operation: str,
        idempotency_key: str,
    ) -> bool:
        """
        Atomically transition an idempotency record
        from IN_PROGRESS to COMPLETED.

        Returns True only when the transition
        actually happened.
        """

        now = datetime.utcnow()

        statement = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.company_id == company_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
                IdempotencyRecord.status
                == IdempotencyStatus.IN_PROGRESS,
            )
            .values(
                status=IdempotencyStatus.COMPLETED,
                updated_at=now,
                finished_at=now,
            )
            .returning(
                IdempotencyRecord.id,
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
        company_id: int,
        operation: str,
        idempotency_key: str,
    ) -> bool:
        """
        Atomically transition an idempotency record
        from IN_PROGRESS to FAILED.

        Returns True only when the transition
        actually happened.
        """

        now = datetime.utcnow()

        statement = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.company_id == company_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
                IdempotencyRecord.status
                == IdempotencyStatus.IN_PROGRESS,
            )
            .values(
                status=IdempotencyStatus.FAILED,
                updated_at=now,
                finished_at=now,
            )
            .returning(
                IdempotencyRecord.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        updated_id = result.scalar_one_or_none()

        return updated_id is not None
    async def try_restart_failed(
        self,
        *,
        company_id: int,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> bool:
        """
        Atomically restart a FAILED idempotency record.

        The retry is allowed only when:

        - the exact idempotency record exists;
        - its status is FAILED;
        - the request fingerprint is unchanged.

        Returns True only when this transaction
        successfully changed FAILED to IN_PROGRESS.
        """

        now = datetime.utcnow()

        statement = (
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.company_id == company_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
                IdempotencyRecord.request_fingerprint
                == request_fingerprint,
                IdempotencyRecord.status
                == IdempotencyStatus.FAILED,
            )
            .values(
                status=IdempotencyStatus.IN_PROGRESS,
                updated_at=now,
                finished_at=None,
            )
            .returning(
                IdempotencyRecord.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        restarted_id = result.scalar_one_or_none()

        return restarted_id is not None
