from app.services.idempotency_record_definition import (
    IdempotencyRecordDefinition,
)


class IdempotencyRecordCatalogError(Exception):
    """Base error for idempotency record catalog operations."""


class IdempotencyRecordNotFoundError(
    IdempotencyRecordCatalogError
):
    """Raised when an idempotency record cannot be found."""


class DuplicateIdempotencyRecordError(
    IdempotencyRecordCatalogError
):
    """Raised when the same idempotency key is registered twice."""


class IdempotencyRecordCatalog:
    def __init__(
        self,
        records: tuple[
            IdempotencyRecordDefinition,
            ...,
        ],
    ) -> None:
        self._by_key: dict[
            tuple[int, str, str],
            IdempotencyRecordDefinition,
        ] = {}

        for record in records:
            key = (
                record.company_id,
                record.operation,
                record.idempotency_key,
            )

            if key in self._by_key:
                raise DuplicateIdempotencyRecordError(
                    "Duplicate idempotency record: "
                    f"company_id={record.company_id}, "
                    f"operation='{record.operation}', "
                    f"idempotency_key='{record.idempotency_key}'"
                )

            self._by_key[key] = record

    def get(
        self,
        company_id: int,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyRecordDefinition:
        key = (
            company_id,
            operation,
            idempotency_key,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise IdempotencyRecordNotFoundError(
                "Idempotency record not found: "
                f"company_id={company_id}, "
                f"operation='{operation}', "
                f"idempotency_key='{idempotency_key}'"
            ) from exc

    def for_company(
        self,
        company_id: int,
    ) -> tuple[
        IdempotencyRecordDefinition,
        ...,
    ]:
        return tuple(
            record
            for record in self._by_key.values()
            if record.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[
        IdempotencyRecordDefinition,
        ...,
    ]:
        return tuple(self._by_key.values())