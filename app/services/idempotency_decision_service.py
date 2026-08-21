from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)
from app.services.idempotency_retry_policy_types import (
    IdempotencyRetryPolicy,
)
from app.services.idempotency_types import (
    IdempotencyStatus,
)


def decide_idempotency_action(
    existing_record: IdempotencyRecordDefinition | None,
    retry_policy: IdempotencyRetryPolicy = (
        IdempotencyRetryPolicy.FORBID
    ),
) -> IdempotencyDecision:
    """
    Decide what should happen with an idempotent request.

    Failed operations are not retryable by default.
    Retry must be explicitly allowed by policy.
    """

    if existing_record is None:
        return IdempotencyDecision.START_NEW

    if existing_record.status == IdempotencyStatus.IN_PROGRESS:
        return IdempotencyDecision.ALREADY_IN_PROGRESS

    if existing_record.status == IdempotencyStatus.COMPLETED:
        return IdempotencyDecision.REUSE_RESULT

    if existing_record.status == IdempotencyStatus.FAILED:
        if retry_policy == IdempotencyRetryPolicy.ALLOW:
            return IdempotencyDecision.RETRY_FAILED

        return IdempotencyDecision.FAILED_NOT_RETRYABLE

    raise ValueError(
        "Unsupported idempotency status: "
        f"{existing_record.status}"
    )