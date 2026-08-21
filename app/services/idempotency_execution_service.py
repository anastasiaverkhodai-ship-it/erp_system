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
from app.services.idempotency_record_catalog import (
    IdempotencyRecordCatalog,
    IdempotencyRecordNotFoundError,
)
from app.services.idempotency_request_validator import (
    validate_idempotency_request,
)
from app.services.idempotency_result_catalog import (
    IdempotencyResultCatalog,
    IdempotencyResultNotFoundError,
)
from app.services.idempotency_retry_policy_types import (
    IdempotencyRetryPolicy,
)


class OrphanIdempotencyResultError(Exception):
    """
    Raised when a stored idempotency result exists
    without its corresponding idempotency record.
    """


def evaluate_idempotency_request(
    company_id: int,
    operation: str,
    idempotency_key: str,
    request_fingerprint: str,
    record_catalog: IdempotencyRecordCatalog,
    result_catalog: IdempotencyResultCatalog,
    retry_policy: IdempotencyRetryPolicy = (
        IdempotencyRetryPolicy.FORBID
    ),
) -> IdempotencyExecutionResult:
    """
    Evaluate an incoming idempotent request.

    The service validates:

    - whether an existing record exists;
    - whether the request fingerprint matches;
    - whether record/result state is consistent;
    - whether failed requests may be retried;
    - which idempotency decision should be returned.
    """

    try:
        existing_record = record_catalog.get(
            company_id=company_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
    except IdempotencyRecordNotFoundError:
        try:
            result_catalog.get(
                company_id=company_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
        except IdempotencyResultNotFoundError:
            return IdempotencyExecutionResult(
                decision=IdempotencyDecision.START_NEW,
            )

        raise OrphanIdempotencyResultError(
            "Idempotency result exists without "
            "a corresponding record: "
            f"company_id={company_id}, "
            f"operation='{operation}', "
            f"idempotency_key='{idempotency_key}'"
        )

    validate_idempotency_request(
        existing_record=existing_record,
        request_fingerprint=request_fingerprint,
    )

    try:
        stored_result = result_catalog.get(
            company_id=company_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
    except IdempotencyResultNotFoundError:
        stored_result = None

    validate_idempotency_consistency(
        record=existing_record,
        result=stored_result,
    )

    decision = decide_idempotency_action(
        existing_record=existing_record,
        retry_policy=retry_policy,
    )

    if decision == IdempotencyDecision.REUSE_RESULT:
        return IdempotencyExecutionResult(
            decision=decision,
            record=existing_record,
            reusable_result=stored_result,
        )

    return IdempotencyExecutionResult(
        decision=decision,
        record=existing_record,
    )