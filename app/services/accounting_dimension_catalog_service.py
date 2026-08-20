from app.services.accounting_dimension_catalog import (
    SYSTEM_ACCOUNTING_DIMENSIONS,
)
from app.services.accounting_dimension_definition import (
    AccountingDimensionDefinition,
)


class AccountingDimensionCatalogError(Exception):
    """Base error for accounting dimension catalog operations."""


class AccountingDimensionNotFoundError(
    AccountingDimensionCatalogError
):
    """Raised when an accounting dimension is not registered."""


class DuplicateAccountingDimensionCodeError(
    AccountingDimensionCatalogError
):
    """Raised when the same accounting dimension code is registered twice."""


class AccountingDimensionCatalog:
    def __init__(
        self,
        dimensions: tuple[
            AccountingDimensionDefinition,
            ...,
        ],
    ) -> None:
        self._dimensions: dict[
            str,
            AccountingDimensionDefinition,
        ] = {}

        for dimension in dimensions:
            if dimension.code in self._dimensions:
                raise DuplicateAccountingDimensionCodeError(
                    "Duplicate accounting dimension code: "
                    f"'{dimension.code}'"
                )

            self._dimensions[dimension.code] = dimension

    def get(
        self,
        code: str,
    ) -> AccountingDimensionDefinition:
        dimension = self._dimensions.get(code)

        if dimension is None:
            raise AccountingDimensionNotFoundError(
                f"Accounting dimension '{code}' is not registered"
            )

        return dimension

    def all(
        self,
    ) -> tuple[AccountingDimensionDefinition, ...]:
        return tuple(self._dimensions.values())


SYSTEM_ACCOUNTING_DIMENSION_CATALOG = AccountingDimensionCatalog(
    dimensions=SYSTEM_ACCOUNTING_DIMENSIONS,
)