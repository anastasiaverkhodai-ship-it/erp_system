from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recalculation_request_repository import (
    RecalculationRequestRepository,
)
from app.services.recalculation_claim_service import (
    RecalculationClaim,
)


class RecalculationLeaseRenewalService:
    """
    Renew the active worker lease for one exact
    PROCESSING recalculation claim.

    Transaction ownership remains with the caller.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        lease_duration: timedelta = timedelta(
            minutes=5
        ),
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError(
                "lease_duration must be positive"
            )

        self._session = session
        self._lease_duration = lease_duration

        self._repository = (
            RecalculationRequestRepository(
                session=session,
            )
        )

    async def renew_claim(
        self,
        *,
        claim: RecalculationClaim,
    ) -> RecalculationClaim:
        """
        Renew one exact PROCESSING claim.

        The persisted claim is locked first.

        renewed_at is captured only AFTER obtaining
        the row locks, so a lease that expires while
        waiting cannot be renewed by a stale worker.

        A new RecalculationClaim containing the new
        lease_expires_at is returned.

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

            renewed_at = datetime.utcnow()

            if renewed_at < claim.started_at:
                raise RuntimeError(
                    "Renewal time cannot be before "
                    "claim start time"
                )

            if any(
                row.lease_expires_at is None
                or row.lease_expires_at <= renewed_at
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

            new_lease_expires_at = (
                renewed_at
                + self._lease_duration
            )

            if (
                new_lease_expires_at
                <= claim.lease_expires_at
            ):
                raise RuntimeError(
                    "Renewed lease must extend "
                    "current lease"
                )

            updated_ids = (
                await self._repository.renew_processing_lease_for_claim(
                    company_id=claim.company_id,
                    domain=claim.domain,
                    stream_key=claim.stream_key,
                    request_ids=claim.request_ids,
                    claimed_by=claim.worker_id,
                    started_at=claim.started_at,
                    current_lease_expires_at=(
                        claim.lease_expires_at
                    ),
                    renewed_at=renewed_at,
                    new_lease_expires_at=(
                        new_lease_expires_at
                    ),
                )
            )

            if set(updated_ids) != set(
                claim.request_ids
            ):
                raise RuntimeError(
                    "Renewed recalculation request set "
                    "does not match claim"
                )

            return RecalculationClaim(
                company_id=claim.company_id,
                domain=claim.domain,
                stream_key=claim.stream_key,
                effective_from=claim.effective_from,
                request_ids=claim.request_ids,
                worker_id=claim.worker_id,
                started_at=claim.started_at,
                lease_expires_at=(
                    new_lease_expires_at
                ),
            )
