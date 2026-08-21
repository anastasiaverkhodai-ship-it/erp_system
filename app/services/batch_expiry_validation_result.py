from dataclasses import dataclass

from app.services.batch_expiry_policy_types import (
    BatchExpiryPolicy,
)
from app.services.batch_expiry_types import (
    BatchExpiryStatus,
)


@dataclass(frozen=True, slots=True)
class BatchExpiryValidationResult:
    """
    Result of applying an expiry policy
    to a batch expiry status.
    """

    status: BatchExpiryStatus
    policy: BatchExpiryPolicy

    @property
    def is_allowed(self) -> bool:
        if self.status != BatchExpiryStatus.EXPIRED:
            return True

        return self.policy != BatchExpiryPolicy.BLOCK

    @property
    def is_blocked(self) -> bool:
        return not self.is_allowed

    @property
    def has_warning(self) -> bool:
        return (
            self.status == BatchExpiryStatus.EXPIRED
            and self.policy == BatchExpiryPolicy.WARN
        )