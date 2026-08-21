from app.services.idempotency_result_definition import (
    IdempotencyResultDefinition,
)


class IdempotencyResultCatalogError(Exception):
    """Base error for idempotency result catalog operations."""


class IdempotencyResultNotFoundError(
    IdempotencyResultCatalogError
):
    """Raised when an idempotency result cannot be found."""


class DuplicateIdempotencyResultError(
    IdempotencyResultCatalogError
):
    """Raised when the same idempotency result is registered twice."""


class IdempotencyResultCatalog:
    def __init__(
        self,
        results: tuple[
            IdempotencyResultDefinition,
            ...,
        ],
    ) -> None:
        self._by_key: dict[
            tuple[int, str, str],
            IdempotencyResultDefinition,
        ] = {}

        for result in results:
            key = (
                result.company_id,
                result.operation,
                result.idempotency_key,
            )

            if key in self._by_key:
                raise DuplicateIdempotencyResultError(
                    "Duplicate idempotency result: "
                    f"company_id={result.company_id}, "
                    f"operation='{result.operation}', "
                    f"idempotency_key='{result.idempotency_key}'"
                )

            self._by_key[key] = result

    def get(
        self,
        company_id: int,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyResultDefinition:
        key = (
            company_id,
            operation,
            idempotency_key,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise IdempotencyResultNotFoundError(
                "Idempotency result not found: "
                f"company_id={company_id}, "
                f"operation='{operation}', "
                f"idempotency_key='{idempotency_key}'"
            ) from exc

    def for_company(
        self,
        company_id: int,
    ) -> tuple[
        IdempotencyResultDefinition,
        ...,
    ]:
        return tuple(
            result
            for result in self._by_key.values()
            if result.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[
        IdempotencyResultDefinition,
        ...,
    ]:
        return tuple(self._by_key.values())