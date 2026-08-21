from enum import StrEnum


class OutboxStatus(StrEnum):
    """
    Lifecycle status of an outbox event.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"