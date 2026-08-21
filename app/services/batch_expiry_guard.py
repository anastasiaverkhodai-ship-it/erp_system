from datetime import date

from app.services.batch_definition import (
    BatchDefinition,
)
from app.services.batch_expiry_policy_types import (
    BatchExpiryPolicy,
)
from app.services.batch_expiry_validation_result import (
    BatchExpiryValidationResult,
)
from app.services.batch_expiry_validation_service import (
    validate_batch_expiry,
)


class BatchExpiryBlockedError(Exception):
    """
    Raised when an expired batch is blocked
    by the selected expiry policy.
    """


def enforce_batch_expiry_policy(
    batch: BatchDefinition,
    evaluated_date: date,
    policy: BatchExpiryPolicy,
) -> BatchExpiryValidationResult:
    """
    Validate batch expiry and raise an error
    when the selected policy blocks its use.

    WARN and ALLOW return a normal result.
    BLOCK raises BatchExpiryBlockedError
    for an expired batch.
    """

    result = validate_batch_expiry(
        batch=batch,
        evaluated_date=evaluated_date,
        policy=policy,
    )

    if result.is_blocked:
        raise BatchExpiryBlockedError(
            "Expired batch cannot be used under "
            "the selected expiry policy: "
            f"company_id={batch.company_id}, "
            f"product_id={batch.product_id}, "
            f"batch_number='{batch.batch_number}', "
            f"expiry_date={batch.expiry_date}, "
            f"evaluated_date={evaluated_date}"
        )

    return result