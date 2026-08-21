from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_result import IdempotencyResult


class IdempotencyResultRepository:
    """
    PostgreSQL persistence operations
    for reusable idempotency results.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

    async def try_create(
        self,
        *,
        idempotency_record_id: int,
        result_type: str,
        result_id: str,
        result_payload: str | None = None,
    ) -> bool:
        """
        Atomically try to create a reusable result.

        Returns:
            True:
                the result was created.

            False:
                a result for this idempotency
                record already exists.

        This method does not commit or rollback.
        """

        statement = (
            insert(IdempotencyResult)
            .values(
                idempotency_record_id=idempotency_record_id,
                result_type=result_type,
                result_id=result_id,
                result_payload=result_payload,
                created_at=datetime.utcnow(),
            )
            .on_conflict_do_nothing(
                constraint="uq_idempotency_result_record",
            )
            .returning(
                IdempotencyResult.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        inserted_id = result.scalar_one_or_none()

        return inserted_id is not None

    async def get_by_record_id(
        self,
        *,
        idempotency_record_id: int,
    ) -> IdempotencyResult | None:
        """
        Return the reusable result belonging
        to an idempotency record.

        Returns None when no result exists.
        """

        statement = select(
            IdempotencyResult
        ).where(
            IdempotencyResult.idempotency_record_id
            == idempotency_record_id
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()