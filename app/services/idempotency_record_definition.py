from dataclasses import dataclass

from app.services.idempotency_types import (
    IdempotencyStatus,
)


@dataclass(frozen=True, slots=True)
class IdempotencyRecordDefinition:
    """
    Definition of an idempotent operation.

    company_id
        Company that owns the operation.

    operation
        Logical operation name, for example:
        document_post
        document_reverse
        payment_create

    idempotency_key
        Client-provided unique key for the operation.

    request_fingerprint
        Stable fingerprint of the request payload.
        It prevents reuse of the same idempotency key
        for a different request.

    status
        Current processing state.
    """

    company_id: int
    operation: str
    idempotency_key: str
    request_fingerprint: str
    status: IdempotencyStatus

    def __post_init__(self) -> None:
        if self.company_id <= 0:
            raise ValueError(
                "Company ID must be greater than zero"
            )

        if not self.operation.strip():
            raise ValueError(
                "Idempotency operation cannot be empty"
            )

        if not self.idempotency_key.strip():
            raise ValueError(
                "Idempotency key cannot be empty"
            )

        if not self.request_fingerprint.strip():
            raise ValueError(
                "Request fingerprint cannot be empty"
            )