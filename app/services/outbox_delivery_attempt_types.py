from enum import StrEnum


class OutboxDeliveryAttemptStatus(StrEnum):
    """
    Lifecycle status of one outbox delivery attempt.
    """

    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"