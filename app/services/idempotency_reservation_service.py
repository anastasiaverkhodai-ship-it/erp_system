from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.idempotency_record_repository import (
    IdempotencyRecordRepository,
)
from app.repositories.idempotency_result_repository import (
    IdempotencyResultRepository,
)
from app.services.idempotency_consistency_validator import (
    validate_idempotency_consistency,
)
from app.services.idempotency_decision_service import (
    decide_idempotency_action,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_execution_result import (
    IdempotencyExecutionResult,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)
from app.services.idempotency_persistence_mapper import (
    map_idempotency_record,
    map_idempotency_result,
)
from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)
from app.services.idempotency_request_validator import (
    validate_idempotency_request,
)
from app.services.idempotency_retry_policy_types import (
    IdempotencyRetryPolicy,
)
from app.services.idempotency_types import (
    IdempotencyStatus,
)


class IdempotencyReservationError(Exception):
    """
    Base error for persisted idempotency reservation.
    """


class IdempotencyReservationRecordMissingError(
    IdempotencyReservationError
):
    """
    Raised when reservation was not acquired,
    but the existing record cannot be read.
    """


class IdempotencyRetryReservationConflictError(
    IdempotencyReservationError
):
    """
    Raised when a FAILED record was eligible for retry,
    but an atomic retry reservation could not be acquired
    and the record is still FAILED.
    """


async def reserve_idempotent_operation(
    *,
    session: AsyncSession,
    company_id: int,
    operation: str,
    idempotency_key: str,
    request_fingerprint: str,
    retry_policy: IdempotencyRetryPolicy = (
        IdempotencyRetryPolicy.FORBID
    ),
) -> IdempotencyExecutionResult:
    """
    Atomically evaluate and reserve an idempotent operation.

    This is the lower-level entry point that accepts
    an already generated request fingerprint.

    The caller owns the transaction.

    This function does not commit or rollback.
    """

    # Validate the incoming business key before
    # attempting to persist it.
    IdempotencyRecordDefinition(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        status=IdempotencyStatus.IN_PROGRESS,
    )

    record_repository = IdempotencyRecordRepository(
        session=session,
    )

    result_repository = IdempotencyResultRepository(
        session=session,
    )

    acquired = await record_repository.try_reserve(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )

    if acquired:
        return IdempotencyExecutionResult(
            decision=IdempotencyDecision.START_NEW,
        )

    record = await record_repository.get_by_key(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )

    if record is None:
        raise IdempotencyReservationRecordMissingError(
            "Idempotency reservation was not acquired, "
            "but the existing record could not be found: "
            f"company_id={company_id}, "
            f"operation='{operation}', "
            f"idempotency_key='{idempotency_key}'"
        )

    domain_record = map_idempotency_record(
        record,
    )

    validate_idempotency_request(
        existing_record=domain_record,
        request_fingerprint=request_fingerprint,
    )

    stored_result = await result_repository.get_by_record_id(
        idempotency_record_id=record.id,
    )

    domain_result = None

    if stored_result is not None:
        domain_result = map_idempotency_result(
            record=record,
            result=stored_result,
        )

    validate_idempotency_consistency(
        record=domain_record,
        result=domain_result,
    )

    decision = decide_idempotency_action(
        existing_record=domain_record,
        retry_policy=retry_policy,
    )

    if decision == IdempotencyDecision.REUSE_RESULT:
        return IdempotencyExecutionResult(
            decision=decision,
            record=domain_record,
            reusable_result=domain_result,
        )

    if decision != IdempotencyDecision.RETRY_FAILED:
        return IdempotencyExecutionResult(
            decision=decision,
            record=domain_record,
        )

    restarted = await record_repository.try_restart_failed(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )

    if restarted:
        restarted_domain_record = IdempotencyRecordDefinition(
            company_id=domain_record.company_id,
            operation=domain_record.operation,
            idempotency_key=domain_record.idempotency_key,
            request_fingerprint=domain_record.request_fingerprint,
            status=IdempotencyStatus.IN_PROGRESS,
        )

        return IdempotencyExecutionResult(
            decision=IdempotencyDecision.RETRY_FAILED,
            record=restarted_domain_record,
        )

    # Another transaction may have changed the FAILED
    # record between evaluation and the atomic restart.
    latest_record = await record_repository.get_by_key(
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
    )

    if latest_record is None:
        raise IdempotencyReservationRecordMissingError(
            "Idempotency record disappeared while "
            "attempting to reserve a retry"
        )

    latest_domain_record = map_idempotency_record(
        latest_record,
    )

    validate_idempotency_request(
        existing_record=latest_domain_record,
        request_fingerprint=request_fingerprint,
    )

    latest_stored_result = (
        await result_repository.get_by_record_id(
            idempotency_record_id=latest_record.id,
        )
    )

    latest_domain_result = None

    if latest_stored_result is not None:
        latest_domain_result = map_idempotency_result(
            record=latest_record,
            result=latest_stored_result,
        )

    validate_idempotency_consistency(
        record=latest_domain_record,
        result=latest_domain_result,
    )

    latest_decision = decide_idempotency_action(
        existing_record=latest_domain_record,
        retry_policy=retry_policy,
    )

    if latest_decision == IdempotencyDecision.REUSE_RESULT:
        return IdempotencyExecutionResult(
            decision=latest_decision,
            record=latest_domain_record,
            reusable_result=latest_domain_result,
        )

    if latest_decision == IdempotencyDecision.RETRY_FAILED:
        raise IdempotencyRetryReservationConflictError(
            "FAILED idempotency record could not be "
            "atomically reserved for retry"
        )

    return IdempotencyExecutionResult(
        decision=latest_decision,
        record=latest_domain_record,
    )


async def reserve_idempotent_request(
    *,
    session: AsyncSession,
    company_id: int,
    operation: str,
    idempotency_key: str,
    request_payload: Any,
    retry_policy: IdempotencyRetryPolicy = (
        IdempotencyRetryPolicy.FORBID
    ),
) -> IdempotencyExecutionResult:
    """
    High-level entry point for reserving an
    idempotent request.

    The request fingerprint is generated automatically
    from the canonical request payload.

    Equivalent payloads with a different dictionary
    key order produce the same fingerprint.

    The caller owns the transaction.

    This function does not commit or rollback.
    """

    request_fingerprint = generate_request_fingerprint(
        request_payload
    )

    return await reserve_idempotent_operation(
        session=session,
        company_id=company_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        retry_policy=retry_policy,
    )