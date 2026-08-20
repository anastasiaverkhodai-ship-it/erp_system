from decimal import Decimal

from app.services.uom_conversion_definition import (
    UnitConversionDefinition,
)
from app.services.uom_conversions import SYSTEM_UNIT_CONVERSIONS


class UnitConversionCatalogError(Exception):
    """Base error for unit conversion catalog operations."""


class UnitConversionNotFoundError(
    UnitConversionCatalogError
):
    """Raised when no conversion exists between two units."""


class DuplicateUnitConversionError(
    UnitConversionCatalogError
):
    """Raised when the same unit pair is registered twice."""


class UnitConversionCatalog:
    def __init__(
        self,
        conversions: tuple[UnitConversionDefinition, ...],
    ) -> None:
        self._conversions: dict[
            tuple[str, str],
            UnitConversionDefinition,
        ] = {}

        for conversion in conversions:
            direct_key = (
                conversion.source_code,
                conversion.target_code,
            )

            reverse_key = (
                conversion.target_code,
                conversion.source_code,
            )

            if (
                direct_key in self._conversions
                or reverse_key in self._conversions
            ):
                raise DuplicateUnitConversionError(
                    "Duplicate unit conversion pair: "
                    f"'{conversion.source_code}' "
                    f"<-> '{conversion.target_code}'"
                )

            self._conversions[direct_key] = conversion

    def get_factor(
        self,
        source_code: str,
        target_code: str,
    ) -> Decimal:
        direct = self._conversions.get(
            (
                source_code,
                target_code,
            )
        )

        if direct is not None:
            return direct.factor

        reverse = self._conversions.get(
            (
                target_code,
                source_code,
            )
        )

        if reverse is not None:
            return Decimal("1") / reverse.factor

        raise UnitConversionNotFoundError(
            "Unit conversion is not registered: "
            f"'{source_code}' -> '{target_code}'"
        )

    def all(
        self,
    ) -> tuple[UnitConversionDefinition, ...]:
        return tuple(self._conversions.values())


SYSTEM_UNIT_CONVERSION_CATALOG = UnitConversionCatalog(
    conversions=SYSTEM_UNIT_CONVERSIONS,
)