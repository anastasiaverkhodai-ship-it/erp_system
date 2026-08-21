from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.services.outbox_claim_service import (
    OutboxClaimService,
)
from app.services.outbox_completion_service import (
    OutboxCompletionService,
)
from app.services.outbox_failure_service import (
    OutboxFailureService,
)
from app.services.outbox_publisher import (
    OutboxPublishMessage,
    OutboxPublisher,
)


class OutboxWorkerOutcome(StrEnum):
    """
    Result of one worker iteration.
    """

    NO_EVENT = "no_event"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(
    frozen=True,
    slots=True,
)
class OutboxWorkerResult:
    """
    Immutable result of one worker iteration.
    """

    outcome: OutboxWorkerOutcome
    event_id: str | None = None
    attempt_number: int | None = None
    error_message: str | None = None


class OutboxWorkerService:
    """
    Process outbox events one at a time.

    Database transactions are intentionally separated
    from the external publish operation.

    Flow:

    1. Claim event and commit claim.
    2. Publish outside the database transaction.
    3. Complete success or failure in a new transaction.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: OutboxPublisher,
        worker_id: str,
        lease_duration: timedelta = timedelta(
            minutes=5,
        ),
    ) -> None:
        if not worker_id.strip():
            raise ValueError(
                "worker_id cannot be empty"
            )

        if lease_duration <= timedelta(0):
            raise ValueError(
                "lease_duration must be positive"
            )

        self._session_factory = session_factory
        self._publisher = publisher
        self._worker_id = worker_id
        self._lease_duration = lease_duration

    async def process_next(
        self,
    ) -> OutboxWorkerResult:
        """
        Process one available outbox event.

        Returns NO_EVENT when no event is available.

        Publisher exceptions are persisted as a
        normal FAILED delivery.

        Database/state failures are not swallowed;
        they propagate to the caller.
        """

        # -------------------------------------------------
        # Phase 1:
        # claim and COMMIT before external I/O
        # -------------------------------------------------

        async with self._session_factory() as session:
            claim_service = OutboxClaimService(
                session=session,
                worker_id=self._worker_id,
                lease_duration=self._lease_duration,
            )

            claim = await claim_service.claim_next()

            if claim is None:
                await session.rollback()

                return OutboxWorkerResult(
                    outcome=OutboxWorkerOutcome.NO_EVENT,
                )

            await session.commit()

        # claim is an immutable dataclass,
        # so it is safe to use after session close.

        message = OutboxPublishMessage(
            event_id=claim.event_id,
            company_id=claim.company_id,
            event_type=claim.event_type,
            aggregate_type=claim.aggregate_type,
            aggregate_id=claim.aggregate_id,
            payload=claim.payload,
            attempt_number=claim.attempt_number,
        )

        # -------------------------------------------------
        # Phase 2:
        # external I/O with no database transaction open
        # -------------------------------------------------

        try:
            await self._publisher.publish(
                message=message,
            )

        except Exception as exc:
            error_message = str(exc).strip()

            if not error_message:
                error_message = type(exc).__name__

            # ---------------------------------------------
            # Phase 3A:
            # persist delivery failure
            # ---------------------------------------------

            async with self._session_factory() as session:
                failure_service = OutboxFailureService(
                    session=session,
                )

                await failure_service.complete_failure(
                    outbox_event_id=claim.outbox_event_id,
                    attempt_number=claim.attempt_number,
                    worker_id=self._worker_id,
                    error_message=error_message,
                )

                await session.commit()

            return OutboxWorkerResult(
                outcome=OutboxWorkerOutcome.FAILED,
                event_id=claim.event_id,
                attempt_number=claim.attempt_number,
                error_message=error_message,
            )

        # -------------------------------------------------
        # Phase 3B:
        # persist successful delivery
        # -------------------------------------------------

        async with self._session_factory() as session:
            completion_service = OutboxCompletionService(
                session=session,
            )

            await completion_service.complete_success(
                outbox_event_id=claim.outbox_event_id,
                attempt_number=claim.attempt_number,
                worker_id=self._worker_id,
            )

            await session.commit()

        return OutboxWorkerResult(
            outcome=OutboxWorkerOutcome.PUBLISHED,
            event_id=claim.event_id,
            attempt_number=claim.attempt_number,
            error_message=None,
        )
