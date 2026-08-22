from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recalculation_request import (
    RecalculationRequest,
)
from app.services.recalculation_request_definition import (
    RecalculationRequestDefinition,
)
from app.services.recalculation_types import (
    RecalculationDomain,
    RecalculationStatus,
)


class RecalculationRequestRepository:
    """
    PostgreSQL persistence operations
    for backdated recalculation requests.

    Transaction ownership stays with the caller.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self._session = session

    async def try_create(
        self,
        *,
        request: RecalculationRequestDefinition,
    ) -> bool:
        """
        Atomically try to persist a new PENDING
        recalculation request.

        Returns:

        True  -> request was created.
        False -> request_id already exists.

        This method does not commit or rollback.
        """

        if request.status != RecalculationStatus.PENDING:
            raise ValueError(
                "New recalculation request must "
                "have PENDING status"
            )

        now = datetime.utcnow()

        statement = (
            insert(RecalculationRequest)
            .values(
                request_id=request.request_id,
                company_id=request.company_id,
                domain=request.domain,
                stream_key=request.stream_key,
                effective_from=request.effective_from,
                status=request.status,
                created_at=request.created_at,
                updated_at=now,
                started_at=None,
                claimed_by=None,
                lease_expires_at=None,
                finished_at=None,
                error_message=None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    RecalculationRequest.request_id,
                ],
            )
            .returning(
                RecalculationRequest.id,
            )
        )

        result = await self._session.execute(
            statement,
        )

        inserted_id = result.scalar_one_or_none()

        return inserted_id is not None

    async def get_by_request_id(
        self,
        *,
        request_id: str,
    ) -> RecalculationRequest | None:
        """
        Return one recalculation request
        by its stable request_id.

        Returns None when it does not exist.
        """

        if not request_id.strip():
            raise ValueError(
                "request_id cannot be empty"
            )

        statement = select(
            RecalculationRequest
        ).where(
            RecalculationRequest.request_id
            == request_id
        )

        result = await self._session.execute(
            statement,
        )

        return result.scalar_one_or_none()

    async def lock_pending_for_stream(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> tuple[
        RecalculationRequest,
        ...
    ]:
        """
        Lock the current PENDING snapshot for one
        recalculation stream.

        Rows are returned from the earliest
        effective_from forward.

        FOR UPDATE prevents another transaction
        from modifying the same selected requests
        until the caller completes its transaction.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        statement = (
            select(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.status
                == RecalculationStatus.PENDING,
            )
            .order_by(
                RecalculationRequest.effective_from.asc(),
                RecalculationRequest.created_at.asc(),
                RecalculationRequest.id.asc(),
            )
            .with_for_update()
        )

        result = await self._session.execute(
            statement,
        )

        return tuple(
            result.scalars().all()
        )

    async def mark_processing_for_requests(
        self,
        *,
        request_ids: tuple[str, ...],
        started_at: datetime,
        claimed_by: str,
        lease_expires_at: datetime,
    ) -> tuple[str, ...]:
        """
        Transition the exact supplied request set:

        PENDING -> PROCESSING

        The caller must already own the transaction
        and should lock the intended snapshot before
        calling this method.

        All supplied requests are expected to still
        be PENDING.

        This method does not commit or rollback.
        """

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if lease_expires_at <= started_at:
            raise ValueError(
                "lease_expires_at must be after started_at"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PENDING,
            )
            .values(
                status=RecalculationStatus.PROCESSING,
                started_at=started_at,
                claimed_by=normalized_claimed_by,
                lease_expires_at=lease_expires_at,
                finished_at=None,
                error_message=None,
                updated_at=started_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all recalculation requests "
                "were transitioned to PROCESSING"
            )

        return updated_ids

    async def lock_claim_scope(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> None:
        """
        Acquire a PostgreSQL transaction-scoped
        advisory lock for one recalculation stream.

        Concurrent claim transactions for the same
        company/domain/stream are serialized.

        The lock is released automatically when the
        caller's transaction ends.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        scope = (
            f"recalculation:"
            f"{company_id}:"
            f"{domain.value}:"
            f"{stream_key}"
        )

        await self._session.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:scope, 0)
                )
                """
            ),
            {
                "scope": scope,
            },
        )

    async def has_processing_for_stream(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> bool:
        """
        Return True when the stream already has
        at least one PROCESSING request.

        Claim services use this after acquiring the
        stream advisory lock so two recalculations
        for the same stream cannot run concurrently.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        statement = (
            select(
                RecalculationRequest.id
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
            )
            .limit(1)
        )

        result = await self._session.execute(
            statement
        )

        return (
            result.scalar_one_or_none()
            is not None
        )

    async def mark_completed_for_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        claimed_by: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> tuple[str, ...]:
        """
        Atomically transition the exact claimed
        recalculation snapshot:

        PROCESSING -> COMPLETED

        Scope, request_ids and started_at must all
        match the original claim.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if finished_at < started_at:
            raise ValueError(
                "finished_at cannot be before started_at"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
                RecalculationRequest.lease_expires_at
                > finished_at,
            )
            .values(
                status=RecalculationStatus.COMPLETED,
                claimed_by=None,
                lease_expires_at=None,
                finished_at=finished_at,
                error_message=None,
                updated_at=finished_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all recalculation claim requests "
                "were transitioned to COMPLETED"
            )

        return updated_ids

    async def mark_failed_for_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        claimed_by: str,
        started_at: datetime,
        failed_at: datetime,
        error_message: str,
    ) -> tuple[str, ...]:
        """
        Atomically transition the exact claimed
        recalculation snapshot:

        PROCESSING -> FAILED

        Scope, request_ids and started_at must all
        match the original claim.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if failed_at < started_at:
            raise ValueError(
                "failed_at cannot be before started_at"
            )

        normalized_error = error_message.strip()

        if not normalized_error:
            raise ValueError(
                "error_message cannot be empty"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
                RecalculationRequest.lease_expires_at
                > failed_at,
            )
            .values(
                status=RecalculationStatus.FAILED,
                claimed_by=None,
                lease_expires_at=None,
                finished_at=failed_at,
                error_message=normalized_error,
                updated_at=failed_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all recalculation claim requests "
                "were transitioned to FAILED"
            )

        return updated_ids

    async def retry_failed_for_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        started_at: datetime,
        failed_at: datetime,
        retried_at: datetime,
    ) -> tuple[str, ...]:
        """
        Atomically return the exact failed
        recalculation snapshot to the queue:

        FAILED -> PENDING

        The original claim start and failure time
        must still match persisted state.

        Runtime lifecycle fields are cleared so the
        requests can participate in a fresh claim.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        if failed_at < started_at:
            raise ValueError(
                "failed_at cannot be before started_at"
            )

        if retried_at < failed_at:
            raise ValueError(
                "retried_at cannot be before failed_at"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.FAILED,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.finished_at
                == failed_at,
            )
            .values(
                status=RecalculationStatus.PENDING,
                started_at=None,
                finished_at=None,
                error_message=None,
                updated_at=retried_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all failed recalculation "
                "requests were returned to PENDING"
            )

        return updated_ids

    async def lock_processing_for_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        started_at: datetime,
        claimed_by: str,
    ) -> tuple[
        RecalculationRequest,
        ...
    ]:
        """
        Lock the exact PROCESSING snapshot owned by
        one recalculation worker.

        The lock must be acquired before completion
        or failure time is captured. This prevents a
        stale pre-lock timestamp from bypassing an
        expired worker lease.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        statement = (
            select(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
            )
            .order_by(
                RecalculationRequest.id.asc()
            )
            .with_for_update()
        )

        result = await self._session.execute(
            statement
        )

        rows = tuple(
            result.scalars().all()
        )

        found_ids = {
            row.request_id
            for row in rows
        }

        if found_ids != set(request_ids):
            raise RuntimeError(
                "PROCESSING recalculation claim "
                "does not match persisted state"
            )

        return rows

    async def find_next_expired_processing(
        self,
        *,
        now: datetime,
    ) -> RecalculationRequest | None:
        """
        Find the oldest expired PROCESSING
        recalculation claim candidate.

        This method intentionally does NOT acquire
        a row lock.

        Recovery will first use this row only to
        discover company/domain/stream, then acquire
        the stream advisory lock and revalidate the
        complete claim snapshot under row locks.

        This avoids locking individual rows from the
        same multi-request claim in different
        recovery workers.
        """

        statement = (
            select(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.started_at.is_not(
                    None
                ),
                RecalculationRequest.claimed_by.is_not(
                    None
                ),
                RecalculationRequest.lease_expires_at.is_not(
                    None
                ),
                RecalculationRequest.lease_expires_at
                <= now,
            )
            .order_by(
                RecalculationRequest.lease_expires_at.asc(),
                RecalculationRequest.id.asc(),
            )
            .limit(1)
        )

        result = await self._session.execute(
            statement
        )

        return result.scalar_one_or_none()

    async def lock_expired_processing_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        claimed_by: str,
        started_at: datetime,
        lease_expires_at: datetime,
        now: datetime,
    ) -> tuple[
        RecalculationRequest,
        ...
    ]:
        """
        Lock the complete expired PROCESSING
        snapshot for one recalculation claim.

        The caller must acquire the stream advisory
        lock before calling this method.

        Exact claim identity is defined by:

        - company_id
        - domain
        - stream_key
        - claimed_by
        - started_at
        - lease_expires_at

        Returns an empty tuple when the candidate is
        no longer an expired PROCESSING claim.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if lease_expires_at <= started_at:
            raise ValueError(
                "lease_expires_at must be after started_at"
            )

        if now < lease_expires_at:
            return ()

        statement = (
            select(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.lease_expires_at
                == lease_expires_at,
                RecalculationRequest.lease_expires_at
                <= now,
            )
            .order_by(
                RecalculationRequest.id.asc()
            )
            .with_for_update()
        )

        result = await self._session.execute(
            statement
        )

        return tuple(
            result.scalars().all()
        )

    async def mark_expired_processing_failed(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        claimed_by: str,
        started_at: datetime,
        lease_expires_at: datetime,
        recovered_at: datetime,
        error_message: str,
    ) -> tuple[str, ...]:
        """
        Recover one exact expired PROCESSING claim:

        PROCESSING -> FAILED

        This transition is specifically for abandoned
        claims whose worker lease has expired.

        Exact persisted claim identity must still
        match:

        - company_id
        - domain
        - stream_key
        - request_ids
        - claimed_by
        - started_at
        - lease_expires_at

        The worker ownership and lease are cleared
        after successful recovery.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if lease_expires_at <= started_at:
            raise ValueError(
                "lease_expires_at must be after started_at"
            )

        if recovered_at < lease_expires_at:
            raise ValueError(
                "recovered_at cannot be before "
                "lease_expires_at"
            )

        normalized_error = error_message.strip()

        if not normalized_error:
            raise ValueError(
                "error_message cannot be empty"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.lease_expires_at
                == lease_expires_at,
                RecalculationRequest.lease_expires_at
                <= recovered_at,
            )
            .values(
                status=RecalculationStatus.FAILED,
                claimed_by=None,
                lease_expires_at=None,
                finished_at=recovered_at,
                error_message=normalized_error,
                updated_at=recovered_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all expired recalculation "
                "requests were recovered to FAILED"
            )

        return updated_ids

    async def renew_processing_lease_for_claim(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
        request_ids: tuple[str, ...],
        claimed_by: str,
        started_at: datetime,
        current_lease_expires_at: datetime,
        renewed_at: datetime,
        new_lease_expires_at: datetime,
    ) -> tuple[str, ...]:
        """
        Atomically renew the worker lease for one
        exact PROCESSING recalculation claim.

        The persisted claim must still match:

        - company_id
        - domain
        - stream_key
        - request_ids
        - claimed_by
        - started_at
        - current lease_expires_at

        The current lease must still be active and
        the new lease must extend it.

        This method does not commit or rollback.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if any(
            not request_id.strip()
            for request_id in request_ids
        ):
            raise ValueError(
                "request_id cannot be empty"
            )

        if len(set(request_ids)) != len(
            request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_claimed_by = claimed_by.strip()

        if not normalized_claimed_by:
            raise ValueError(
                "claimed_by cannot be empty"
            )

        if len(normalized_claimed_by) > 255:
            raise ValueError(
                "claimed_by cannot exceed 255 characters"
            )

        if current_lease_expires_at <= started_at:
            raise ValueError(
                "current lease must be after started_at"
            )

        if renewed_at < started_at:
            raise ValueError(
                "renewed_at cannot be before started_at"
            )

        if renewed_at >= current_lease_expires_at:
            raise ValueError(
                "Cannot renew an expired lease"
            )

        if (
            new_lease_expires_at
            <= current_lease_expires_at
        ):
            raise ValueError(
                "New lease must extend current lease"
            )

        statement = (
            update(
                RecalculationRequest
            )
            .where(
                RecalculationRequest.company_id
                == company_id,
                RecalculationRequest.domain
                == domain,
                RecalculationRequest.stream_key
                == stream_key,
                RecalculationRequest.request_id.in_(
                    request_ids
                ),
                RecalculationRequest.status
                == RecalculationStatus.PROCESSING,
                RecalculationRequest.claimed_by
                == normalized_claimed_by,
                RecalculationRequest.started_at
                == started_at,
                RecalculationRequest.lease_expires_at
                == current_lease_expires_at,
                RecalculationRequest.lease_expires_at
                > renewed_at,
            )
            .values(
                lease_expires_at=new_lease_expires_at,
                updated_at=renewed_at,
            )
            .returning(
                RecalculationRequest.request_id
            )
        )

        result = await self._session.execute(
            statement
        )

        updated_ids = tuple(
            result.scalars().all()
        )

        if set(updated_ids) != set(
            request_ids
        ):
            raise RuntimeError(
                "Not all recalculation claim leases "
                "were renewed"
            )

        return updated_ids
