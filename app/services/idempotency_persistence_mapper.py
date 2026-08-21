from app.models.idempotency_record import (
    IdempotencyRecord,
)
from app.models.idempotency_result import (
    IdempotencyResult,
)
from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)
from app.services.idempotency_result_definition import (
    IdempotencyResultDefinition,
)


def map_idempotency_record(
    record: IdempotencyRecord,
) -> IdempotencyRecordDefinition:
    """
    Convert a persisted SQLAlchemy idempotency record
    into the pure domain definition.
    """

    return IdempotencyRecordDefinition(
        company_id=record.company_id,
        operation=record.operation,
        idempotency_key=record.idempotency_key,
        request_fingerprint=record.request_fingerprint,
        status=record.status,
    )


def map_idempotency_result(
    *,
    record: IdempotencyRecord,
    result: IdempotencyResult,
) -> IdempotencyResultDefinition:
    """
    Convert a persisted SQLAlchemy reusable result
    into the pure domain definition.

    company_id, operation and idempotency_key are
    taken from the owning idempotency record.
    """

    if result.idempotency_record_id != record.id:
        raise ValueError(
            "Idempotency result does not belong "
            "to the supplied idempotency record"
        )

    return IdempotencyResultDefinition(
        company_id=record.company_id,
        operation=record.operation,
        idempotency_key=record.idempotency_key,
        result_type=result.result_type,
        result_id=result.result_id,
        result_payload=result.result_payload,
    )