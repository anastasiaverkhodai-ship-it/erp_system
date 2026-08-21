from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)
from app.services.idempotency_result_definition import (
    IdempotencyResultDefinition,
)
from app.services.idempotency_types import (
    IdempotencyStatus,
)


class IdempotencyConsistencyError(Exception):
    """Base error for idempotency consistency validation."""


class CompletedIdempotencyResultMissingError(
    IdempotencyConsistencyError
):
    """
    Raised when a COMPLETED record has no stored result.
    """


class UnexpectedIdempotencyResultError(
    IdempotencyConsistencyError
):
    """
    Raised when a non-completed record already has
    a completed result.
    """


class IdempotencyResultMismatchError(
    IdempotencyConsistencyError
):
    """
    Raised when a result belongs to a different
    idempotency operation.
    """


def validate_idempotency_consistency(
    record: IdempotencyRecordDefinition,
    result: IdempotencyResultDefinition | None,
) -> None:
    """
    Validate consistency between an idempotency
    record and its stored result.
    """

    if record.status == IdempotencyStatus.COMPLETED:
        if result is None:
            raise CompletedIdempotencyResultMissingError(
                "Completed idempotency record must have "
                "a stored result"
            )

    else:
        if result is not None:
            raise UnexpectedIdempotencyResultError(
                "Only a completed idempotency record "
                "may have a stored result"
            )

        return

    if (
        result.company_id != record.company_id
        or result.operation != record.operation
        or result.idempotency_key
        != record.idempotency_key
    ):
        raise IdempotencyResultMismatchError(
            "Idempotency result does not match record: "
            f"record=({record.company_id}, "
            f"'{record.operation}', "
            f"'{record.idempotency_key}'), "
            f"result=({result.company_id}, "
            f"'{result.operation}', "
            f"'{result.idempotency_key}')"
        )