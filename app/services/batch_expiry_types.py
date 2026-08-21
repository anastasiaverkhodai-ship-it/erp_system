from enum import StrEnum


class BatchExpiryStatus(StrEnum):
    """
    Expiry state of a product batch.

    NO_EXPIRY
        The batch has no expiration date.

    VALID
        The batch is still valid on the evaluated date.

    EXPIRED
        The batch expiration date has already passed.
    """

    NO_EXPIRY = "no_expiry"
    VALID = "valid"
    EXPIRED = "expired"