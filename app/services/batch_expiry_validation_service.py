from datetime import date

from app.services.batch_definition import (
    BatchDefinition,
)
from app.services.batch_expiry_policy_types import (
    BatchExpiryPolicy,
)
from app.services.batch_expiry_service import (
    get_batch_expiry_status,
)
from app.services.batch_expiry_validation_result import (
    BatchExpiryValidationResult,
)


def validate_batch_expiry(
    batch: BatchDefinition,
    evaluated_date: date,
    policy: BatchExpiryPolicy,
) -> BatchExpiryValidationResult:
    """
    Evaluate a batch expiry state under a selected policy.
    """

    status = get_batch_expiry_status(
        batch=batch,
        evaluated_date=evaluated_date,
    )

    return BatchExpiryValidationResult(
        status=status,
        policy=policy,
    )