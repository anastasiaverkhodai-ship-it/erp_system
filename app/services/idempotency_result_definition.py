from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdempotencyResultDefinition:
    """
    Stored result of a completed idempotent operation.

    company_id
        Company that owns the operation.

    operation
        Logical operation name.

    idempotency_key
        Idempotency key of the completed request.

    result_type
        Logical type of the produced result.
        Examples:
            document
            payment
            journal_entry

    result_id
        Identifier of the produced business resource.

    result_payload
        Optional serialized representation of the
        result that may be reused by the caller.
    """

    company_id: int
    operation: str
    idempotency_key: str
    result_type: str
    result_id: str
    result_payload: str | None = None

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

        if not self.result_type.strip():
            raise ValueError(
                "Idempotency result type cannot be empty"
            )

        if not self.result_id.strip():
            raise ValueError(
                "Idempotency result ID cannot be empty"
            )

        if (
            self.result_payload is not None
            and not self.result_payload.strip()
        ):
            raise ValueError(
                "Idempotency result payload cannot be empty "
                "when provided"
            )