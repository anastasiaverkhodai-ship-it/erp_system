from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recalculation_request_repository import (
    RecalculationRequestRepository,
)
from app.services.recalculation_types import (
    RecalculationDomain,
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationClaim:
    """
    Immutable snapshot of one recalculation
    window successfully claimed by a worker.
    """

    company_id: int
    domain: RecalculationDomain
    stream_key: str
    effective_from: datetime
    request_ids: tuple[str, ...]
    worker_id: str
    started_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not self.stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        if not self.request_ids:
            raise ValueError(
                "request_ids cannot be empty"
            )

        if len(set(self.request_ids)) != len(
            self.request_ids
        ):
            raise ValueError(
                "request_ids cannot contain duplicates"
            )

        normalized_worker_id = self.worker_id.strip()

        if not normalized_worker_id:
            raise ValueError(
                "worker_id cannot be empty"
            )

        if len(normalized_worker_id) > 255:
            raise ValueError(
                "worker_id cannot exceed 255 characters"
            )

        if self.lease_expires_at <= self.started_at:
            raise ValueError(
                "lease_expires_at must be after started_at"
            )

        object.__setattr__(
            self,
            "worker_id",
            normalized_worker_id,
        )

    @property
    def request_count(
        self,
    ) -> int:
        return len(
            self.request_ids
        )


class RecalculationClaimService:
    """
    Atomically claim the current PENDING
    recalculation window for one ERP stream.

    Transaction ownership remains with the caller.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        worker_id: str,
        lease_duration: timedelta = timedelta(
            minutes=5
        ),
    ) -> None:
        normalized_worker_id = worker_id.strip()

        if not normalized_worker_id:
            raise ValueError(
                "worker_id cannot be empty"
            )

        if len(normalized_worker_id) > 255:
            raise ValueError(
                "worker_id cannot exceed 255 characters"
            )

        if lease_duration <= timedelta(0):
            raise ValueError(
                "lease_duration must be positive"
            )

        self._session = session
        self._worker_id = normalized_worker_id
        self._lease_duration = lease_duration

        self._repository = (
            RecalculationRequestRepository(
                session=session,
            )
        )

    async def claim_stream(
        self,
        *,
        company_id: int,
        domain: RecalculationDomain,
        stream_key: str,
    ) -> RecalculationClaim | None:
        """
        Claim one stream for recalculation.

        Returns None when:

        - another recalculation for the stream
          is already PROCESSING; or
        - the stream has no PENDING requests.

        The database changes are protected by a
        nested transaction/savepoint.

        The caller still owns the outer transaction
        and must commit or rollback it.
        """

        if company_id <= 0:
            raise ValueError(
                "company_id must be positive"
            )

        if not stream_key.strip():
            raise ValueError(
                "stream_key cannot be empty"
            )

        async with self._session.begin_nested():
            await self._repository.lock_claim_scope(
                company_id=company_id,
                domain=domain,
                stream_key=stream_key,
            )

            already_processing = (
                await self._repository.has_processing_for_stream(
                    company_id=company_id,
                    domain=domain,
                    stream_key=stream_key,
                )
            )

            if already_processing:
                return None

            pending = (
                await self._repository.lock_pending_for_stream(
                    company_id=company_id,
                    domain=domain,
                    stream_key=stream_key,
                )
            )

            if not pending:
                return None

            request_ids = tuple(
                request.request_id
                for request in pending
            )

            effective_from = (
                pending[0].effective_from
            )

            started_at = datetime.utcnow()

            lease_expires_at = (
                started_at
                + self._lease_duration
            )

            updated_ids = (
                await self._repository.mark_processing_for_requests(
                    request_ids=request_ids,
                    started_at=started_at,
                    claimed_by=self._worker_id,
                    lease_expires_at=lease_expires_at,
                )
            )

            if set(updated_ids) != set(
                request_ids
            ):
                raise RuntimeError(
                    "Claimed recalculation request set "
                    "does not match transitioned set"
                )

            return RecalculationClaim(
                company_id=company_id,
                domain=domain,
                stream_key=stream_key,
                effective_from=effective_from,
                request_ids=request_ids,
                worker_id=self._worker_id,
                started_at=started_at,
                lease_expires_at=lease_expires_at,
            )
