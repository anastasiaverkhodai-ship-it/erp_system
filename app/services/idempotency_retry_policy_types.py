from enum import StrEnum


class IdempotencyRetryPolicy(StrEnum):
    """
    Defines whether a failed idempotent operation
    may be started again with the same key.
    """

    FORBID = "forbid"
    ALLOW = "allow"