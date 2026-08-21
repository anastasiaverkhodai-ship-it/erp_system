from datetime import date

from app.services.batch_definition import (
    BatchDefinition,
)
from app.services.batch_expiry_types import (
    BatchExpiryStatus,
)


def get_batch_expiry_status(
    batch: BatchDefinition,
    evaluated_date: date,
) -> BatchExpiryStatus:
    """
    Determine batch expiry status on a specific date.

    The expiry date itself is still considered valid.

    Example:

        expiry_date = 2026-08-31

        evaluated_date = 2026-08-31
        -> VALID

        evaluated_date = 2026-09-01
        -> EXPIRED
    """

    if batch.expiry_date is None:
        return BatchExpiryStatus.NO_EXPIRY

    if evaluated_date <= batch.expiry_date:
        return BatchExpiryStatus.VALID

    return BatchExpiryStatus.EXPIRED