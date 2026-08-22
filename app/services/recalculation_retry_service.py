from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recalculation_request_repository import (
    RecalculationRequestRepository,
)
from app.services.recalculation_claim_service import (
    RecalculationClaim,
)
from app.services.recalculation_failure_service import (
    RecalculationFailure,
)


@dataclass(
    frozen=True,
    slots=True,
)
class RecalculationRetry:
    """
    Immutable result of returning one failed
    recalculation snapshot back to PENDING.
    """

    request_ids: tuple[str, ...]
    retried_at: datetime

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


class RecalculationRetryService:
    """
    Atomically return one exact failed
    recalculation snapshot to the PENDING queue.

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

    async def retry_failure(
        self,
        *,
        claim: RecalculationClaim,
        failure: RecalculationFailure,
    ) -> RecalculationRetry:
        """
        Transition exactly the failed claim snapshot:

        FAILED -> PENDING

        The claim and failure must describe exactly
        the same request_ids.

        The same stream advisory lock used by claim
        operations is acquired before retrying.

        The caller must commit or rollback the outer
        transaction.
        """

        if set(failure.request_ids) != set(
            claim.request_ids
        ):
            raise ValueError(
                "Failure request_ids do not match claim"
            )

        if failure.failed_at < claim.started_at:
            raise ValueError(
                "Failure time cannot be before "
                "claim start time"
            )

        async with self._session.begin_nested():
            await self._repository.lock_claim_scope(
                company_id=claim.company_id,
                domain=claim.domain,
                stream_key=claim.stream_key,
            )

            # Capture the retry time only after the
            # stream lock has actually been acquired.
            retried_at = datetime.utcnow()

            if retried_at < failure.failed_at:
                raise RuntimeError(
                    "Retry time cannot be before "
                    "failure time"
                )

            updated_ids = (
                await self._repository.retry_failed_for_claim(
                    company_id=claim.company_id,
                    domain=claim.domain,
                    stream_key=claim.stream_key,
                    request_ids=claim.request_ids,
                    started_at=claim.started_at,
                    failed_at=failure.failed_at,
                    retried_at=retried_at,
                )
            )

            if set(updated_ids) != set(
                claim.request_ids
            ):
                raise RuntimeError(
                    "Retried recalculation request set "
                    "does not match claim"
                )

            return RecalculationRetry(
                request_ids=claim.request_ids,
                retried_at=retried_at,
            )
