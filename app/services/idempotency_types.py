from enum import StrEnum


class IdempotencyStatus(StrEnum):
    """
    Processing state of an idempotent operation.

    IN_PROGRESS
        The operation has been accepted but has not
        completed yet.

    COMPLETED
        The operation completed successfully and
        repeated requests must reuse its result.

    FAILED
        The operation failed and did not complete
        successfully.
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"