from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_result import IdempotencyResult
from app.repositories.idempotency_record_repository import (
    IdempotencyRecordRepository,
)
from app.repositories.idempotency_result_repository import (
    IdempotencyResultRepository,
)
from app.services.idempotency_types import IdempotencyStatus


class IdempotencyCompletionError(Exception):
    """
    Base error for idempotency completion.
    """


class IdempotencyCompletionRecordNotFoundError(
    IdempotencyCompletionError
):
    """
    Raised when the idempotency record does not exist.
    """


class IdempotencyCompletionFingerprintMismatchError(
    IdempotencyCompletionError
):
    """
    Raised when completion uses a different
    request fingerprint.
    """


class IdempotencyCompletionStateError(
    IdempotencyCompletionError
):
    """
    Raised when the record cannot transition
    from IN_PROGRESS to COMPLETED.
    """


class IdempotencyCompletionResultConflictError(
    IdempotencyCompletionError
):
    """
    Raised when a reusable result already exists
    for the idempotency record.
    """


class IdempotencyCompletionResultMissingError(
    IdempotencyCompletionError
):
    """
    Raised when the newly created reusable result
    cannot be read back.
    """


async def complete_idempotent_operation(
    *,
    session: AsyncSession,
    company_id: int,
    operation: str,
    idempotency_key: str,
    request_fingerprint: str,
    result_type: str,
    result_id: str,
    result_payload: str | None = None,
) -> IdempotencyResult:
    """
    Complete an idempotent operation.

    The caller owns the transaction.

    This function does not commit or rollback.

    A successful transaction must contain both:

    - IN_PROGRESS -> COMPLETED transition;
    - reusable result creation.
    """

    record_repository = IdempotencyRecordRepository(
        session=session,
    )

    result_repository = IdempotencyResultRepository(
        session=session,
    )

    record = await record_repository.get_by_key(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )

    if record is None:
        raise IdempotencyCompletionRecordNotFoundError(
            "Idempotency record not found: "
            f"company_id={company_id}, "
            f"operation='{operation}', "
            f"idempotency_key='{idempotency_key}'"
        )

    if record.request_fingerprint != request_fingerprint:
        raise IdempotencyCompletionFingerprintMismatchError(
            "Idempotency request fingerprint does not match "
            "the stored reservation"
        )

    if record.status != IdempotencyStatus.IN_PROGRESS:
        raise IdempotencyCompletionStateError(
            "Idempotency record must be IN_PROGRESS "
            "before completion: "
            f"current_status='{record.status.value}'"
        )

    completed = await record_repository.mark_completed(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )

    if not completed:
        raise IdempotencyCompletionStateError(
            "Idempotency record could not transition "
            "from IN_PROGRESS to COMPLETED"
        )

    created = await result_repository.try_create(
        idempotency_record_id=record.id,
        result_type=result_type,
        result_id=result_id,
        result_payload=result_payload,
    )

    if not created:
        raise IdempotencyCompletionResultConflictError(
            "Reusable idempotency result already exists: "
            f"idempotency_record_id={record.id}"
        )

    stored_result = await result_repository.get_by_record_id(
        idempotency_record_id=record.id,
    )

    if stored_result is None:
        raise IdempotencyCompletionResultMissingError(
            "Reusable idempotency result was created "
            "but could not be read back"
        )

    return stored_result