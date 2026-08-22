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
class RecalculationCompletion:
    """
    Immutable result of successfully completing
    one claimed recalculation window.
    """

    request_ids: tuple[str, ...]
    finished_at: datetime

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

    @property
    def request_count(
        self,
    ) -> int:
        return len(
            self.request_ids
        )


class RecalculationCompletionService:
    """
    Atomically complete one previously claimed
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

    async def complete_claim(
        self,
        *,
        claim: RecalculationClaim,
    ) -> RecalculationCompletion:
        """
        Transition exactly the requests belonging
        to the supplied claim:

        PROCESSING -> COMPLETED

        The persisted claim is locked first.

        Completion time is captured only AFTER
        acquiring the row locks. This prevents a
        stale timestamp from bypassing an expired
        worker lease.

        The caller must commit or rollback the outer
        transaction.
        """

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

            finished_at = datetime.utcnow()

            if finished_at < claim.started_at:
                raise RuntimeError(
                    "Completion time cannot be before "
                    "claim start time"
                )

            if any(
                row.lease_expires_at is None
                or row.lease_expires_at <= finished_at
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
                await self._repository.mark_completed_for_claim(
                    company_id=claim.company_id,
                    domain=claim.domain,
                    stream_key=claim.stream_key,
                    request_ids=claim.request_ids,
                    claimed_by=claim.worker_id,
                    started_at=claim.started_at,
                    finished_at=finished_at,
                )
            )

            if set(updated_ids) != set(
                claim.request_ids
            ):
                raise RuntimeError(
                    "Completed recalculation request set "
                    "does not match claim"
                )

            return RecalculationCompletion(
                request_ids=claim.request_ids,
                finished_at=finished_at,
            )
