from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)


class IdempotencyRequestValidationError(Exception):
    """Base error for idempotency request validation."""


class IdempotencyKeyReuseError(
    IdempotencyRequestValidationError
):
    """
    Raised when an existing idempotency key is reused
    for a request with a different fingerprint.
    """


def validate_idempotency_request(
    existing_record: IdempotencyRecordDefinition,
    request_fingerprint: str,
) -> None:
    """
    Validate that a repeated request represents
    the same original request.

    Reusing the same idempotency key is allowed only
    when the request fingerprint is identical.
    """

    if not request_fingerprint.strip():
        raise ValueError(
            "Request fingerprint cannot be empty"
        )

    if (
        existing_record.request_fingerprint
        != request_fingerprint
    ):
        raise IdempotencyKeyReuseError(
            "Idempotency key cannot be reused "
            "for a different request: "
            f"company_id={existing_record.company_id}, "
            f"operation='{existing_record.operation}', "
            f"idempotency_key="
            f"'{existing_record.idempotency_key}', "
            f"existing_fingerprint="
            f"'{existing_record.request_fingerprint}', "
            f"request_fingerprint="
            f"'{request_fingerprint}'"
        )