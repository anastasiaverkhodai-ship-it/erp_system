from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recalculation_request_repository import (
    RecalculationRequestRepository,
)
from app.services.recalculation_claim_service import (
    RecalculationClaim,
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationFailure:
    """
    Immutable result of failing one claimed
    recalculation window.
    """

    request_ids: tuple[str, ...]
    failed_at: datetime
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

        normalized_error = self.error_message.strip()

        if not normalized_error:
            raise ValueError(
                "error_message cannot be empty"
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


class RecalculationFailureService:
    """
    Atomically fail one previously claimed
    recalculation window.

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

    async def fail_claim(
        self,
        *,
        claim: RecalculationClaim,
        error_message: str,
    ) -> RecalculationFailure:
        """
        Transition exactly the requests belonging
        to the supplied claim:

        PROCESSING -> FAILED

        The persisted claim is locked first.

        Failure time is captured only AFTER
        acquiring the row locks. This prevents a
        stale timestamp from bypassing an expired
        worker lease.

        The caller must commit or rollback the outer
        transaction.
        """

        normalized_error = error_message.strip()

        if not normalized_error:
            raise ValueError(
                "error_message cannot be empty"
            )

        async with self._session.begin_nested():
            locked_rows = (
                await self._repository.lock_processing_for_claim(
                    company_id=claim.company_id,
                    domain=claim.domain,
                    stream_key=claim.stream_key,
                    request_ids=claim.request_ids,
                    started_at=claim.started_at,
                    claimed_by=claim.worker_id,
                )
            )

            failed_at = datetime.utcnow()

            if failed_at < claim.started_at:
                raise RuntimeError(
                    "Failure time cannot be before "
                    "claim start time"
                )

            if any(
                row.lease_expires_at is None
                or row.lease_expires_at <= failed_at
                for row in locked_rows
            ):
                raise RuntimeError(
                    "Recalculation claim lease "
                    "has expired"
                )

            if any(
                row.lease_expires_at
                != claim.lease_expires_at
                for row in locked_rows
            ):
                raise RuntimeError(
                    "Recalculation claim lease "
                    "does not match persisted state"
                )

            updated_ids = (
                await self._repository.mark_failed_for_claim(
                    company_id=claim.company_id,
                    domain=claim.domain,
                    stream_key=claim.stream_key,
                    request_ids=claim.request_ids,
                    claimed_by=claim.worker_id,
                    started_at=claim.started_at,
                    failed_at=failed_at,
                    error_message=normalized_error,
                )
            )

            if set(updated_ids) != set(
                claim.request_ids
            ):
                raise RuntimeError(
                    "Failed recalculation request set "
                    "does not match claim"
                )

            return RecalculationFailure(
                request_ids=claim.request_ids,
                failed_at=failed_at,
                error_message=normalized_error,
            )
