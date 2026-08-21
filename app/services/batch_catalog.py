from app.services.batch_definition import (
    BatchDefinition,
)


class BatchCatalogError(Exception):
    """Base error for batch catalog operations."""


class BatchNotFoundError(BatchCatalogError):
    """Raised when a batch cannot be found."""


class DuplicateBatchError(BatchCatalogError):
    """Raised when the same batch is registered twice."""


class BatchCatalog:
    def __init__(
        self,
        batches: tuple[BatchDefinition, ...],
    ) -> None:
        self._by_key: dict[
            tuple[int, int, str],
            BatchDefinition,
        ] = {}

        for batch in batches:
            key = (
                batch.company_id,
                batch.product_id,
                batch.batch_number,
            )

            if key in self._by_key:
                raise DuplicateBatchError(
                    "Duplicate batch: "
                    f"company_id={batch.company_id}, "
                    f"product_id={batch.product_id}, "
                    f"batch_number='{batch.batch_number}'"
                )

            self._by_key[key] = batch

    def get(
        self,
        company_id: int,
        product_id: int,
        batch_number: str,
    ) -> BatchDefinition:
        key = (
            company_id,
            product_id,
            batch_number,
        )

        try:
            return self._by_key[key]
        except KeyError as exc:
            raise BatchNotFoundError(
                "Batch not found: "
                f"company_id={company_id}, "
                f"product_id={product_id}, "
                f"batch_number='{batch_number}'"
            ) from exc

    def for_product(
        self,
        company_id: int,
        product_id: int,
    ) -> tuple[BatchDefinition, ...]:
        return tuple(
            batch
            for batch in self._by_key.values()
            if (
                batch.company_id == company_id
                and batch.product_id == product_id
            )
        )

    def for_company(
        self,
        company_id: int,
    ) -> tuple[BatchDefinition, ...]:
        return tuple(
            batch
            for batch in self._by_key.values()
            if batch.company_id == company_id
        )

    def all(
        self,
    ) -> tuple[BatchDefinition, ...]:
        return tuple(self._by_key.values())