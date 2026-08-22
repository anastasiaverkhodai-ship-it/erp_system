from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recalculation_request_repository import (
    RecalculationRequestRepository,
)


RECOVERY_ERROR_MESSAGE = (
    "Worker lease expired before recalculation "
    "was completed"
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationRecovery:
    """
    Immutable result of recovering one abandoned
    recalculation claim.
    """

    request_ids: tuple[str, ...]
    previous_worker_id: str
    recovered_at: datetime
    error_message: str

    def __post_init__(self) -> None:
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

        normalized_worker = (
            self.previous_worker_id.strip()
        )

        if not normalized_worker:
            raise ValueError(
                "previous_worker_id cannot be empty"
            )

        normalized_error = (
            self.error_message.strip()
        )

        if not normalized_error:
            raise ValueError(
                "error_message cannot be empty"
            )

        object.__setattr__(
            self,
            "previous_worker_id",
            normalized_worker,
        )

        object.__setattr__(
            self,
            "error_message",
            normalized_error,
        )

    @property
    def request_count(
        self,
    ) -> int:
        return len(
            self.request_ids
        )


class RecalculationRecoveryService:
    """
    Recover abandoned PROCESSING recalculation
    claims whose worker lease has expired.

    Transaction ownership remains with the caller.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
    ) -> None:
        self._session = session

        self._repository = (
            RecalculationRequestRepository(
                session=session,
            )
        )

    async def recover_next_expired(
        self,
    ) -> RecalculationRecovery | None:
        """
        Recover the next expired PROCESSING claim.

        Returns None when:

        - no expired candidate exists; or
        - the candidate became stale before this
          recovery worker acquired its stream lock.

        The caller must commit or rollback the outer
        transaction.
        """

        async with self._session.begin_nested():
            discovery_time = datetime.utcnow()

            candidate = (
                await self._repository.find_next_expired_processing(
                    now=discovery_time,
                )
            )

            if candidate is None:
                return None

            if candidate.claimed_by is None:
                raise RuntimeError(
                    "Expired recalculation candidate "
                    "has no worker"
                )

            if candidate.started_at is None:
                raise RuntimeError(
                    "Expired recalculation candidate "
                    "has no started_at"
                )

            if candidate.lease_expires_at is None:
                raise RuntimeError(
                    "Expired recalculation candidate "
                    "has no lease_expires_at"
                )

            company_id = candidate.company_id
            domain = candidate.domain
            stream_key = candidate.stream_key
            claimed_by = candidate.claimed_by
            started_at = candidate.started_at
            lease_expires_at = (
                candidate.lease_expires_at
            )

            # Serialize recovery/claim operations
            # for this exact logical stream.
            await self._repository.lock_claim_scope(
                company_id=company_id,
                domain=domain,
                stream_key=stream_key,
            )

            # Revalidate and lock the COMPLETE claim
            # snapshot only after the stream lock.
            locked_rows = (
                await self._repository.lock_expired_processing_claim(
                    company_id=company_id,
                    domain=domain,
                    stream_key=stream_key,
                    claimed_by=claimed_by,
                    started_at=started_at,
                    lease_expires_at=lease_expires_at,
                    now=datetime.utcnow(),
                )
            )

            if not locked_rows:
                return None

            recovered_at = datetime.utcnow()

            if recovered_at < lease_expires_at:
                raise RuntimeError(
                    "Recovery time cannot be before "
                    "lease expiration"
                )

            request_ids = tuple(
                row.request_id
                for row in locked_rows
            )

            updated_ids = (
                await self._repository.mark_expired_processing_failed(
                    company_id=company_id,
                    domain=domain,
                    stream_key=stream_key,
                    request_ids=request_ids,
                    claimed_by=claimed_by,
                    started_at=started_at,
                    lease_expires_at=lease_expires_at,
                    recovered_at=recovered_at,
                    error_message=RECOVERY_ERROR_MESSAGE,
                )
            )

            if set(updated_ids) != set(
                request_ids
            ):
                raise RuntimeError(
                    "Recovered recalculation request "
                    "set does not match locked claim"
                )

            return RecalculationRecovery(
                request_ids=request_ids,
                previous_worker_id=claimed_by,
                recovered_at=recovered_at,
                error_message=RECOVERY_ERROR_MESSAGE,
            )
