from app.services.uom_catalog import SYSTEM_UNITS
from app.services.uom_definition import UnitOfMeasureDefinition


class UnitOfMeasureCatalogError(Exception):
    """Base error for unit of measure catalog operations."""


class UnitOfMeasureNotFoundError(
    UnitOfMeasureCatalogError
):
    """Raised when a unit of measure code is not registered."""


class DuplicateUnitOfMeasureCodeError(
    UnitOfMeasureCatalogError
):
    """Raised when the same unit code is registered twice."""


class UnitOfMeasureCatalog:
    def __init__(
        self,
        units: tuple[UnitOfMeasureDefinition, ...],
    ) -> None:
        self._units: dict[
            str,
            UnitOfMeasureDefinition,
        ] = {}

        for unit in units:
            if unit.code in self._units:
                raise DuplicateUnitOfMeasureCodeError(
                    f"Duplicate unit of measure code: "
                    f"'{unit.code}'"
                )

            self._units[unit.code] = unit

    def get(
        self,
        code: str,
    ) -> UnitOfMeasureDefinition:
        unit = self._units.get(code)

        if unit is None:
            raise UnitOfMeasureNotFoundError(
                f"Unit of measure '{code}' is not registered"
            )

        return unit

    def all(
        self,
    ) -> tuple[UnitOfMeasureDefinition, ...]:
        return tuple(self._units.values())


SYSTEM_UOM_CATALOG = UnitOfMeasureCatalog(
    units=SYSTEM_UNITS,
)