from enum import StrEnum


class BatchExpiryPolicy(StrEnum):
    """
    Policy for handling expired inventory batches.

    ALLOW
        Expired batches may still be used.

    WARN
        Expired batches may be used, but the system
        should return or display a warning.

    BLOCK
        Expired batches cannot be used.
    """

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"