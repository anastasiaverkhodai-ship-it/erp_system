from dataclasses import dataclass
from decimal import Decimal

from app.services.uom_definition import UnitOfMeasureDefinition


@dataclass(frozen=True, slots=True)
class UnitConversionDefinition:
    """
    Immutable general conversion between compatible units.

    factor means:

        target_quantity = source_quantity * factor

    Example:

        kg -> g
        factor = 1000
    """

    source_unit: UnitOfMeasureDefinition
    target_unit: UnitOfMeasureDefinition
    factor: Decimal

    def __post_init__(self) -> None:
        if self.source_unit.code == self.target_unit.code:
            raise ValueError(
                "Source and target units must be different"
            )

        if (
            self.source_unit.dimension
            != self.target_unit.dimension
        ):
            raise ValueError(
                "Cannot convert units with different dimensions"
            )

        if self.factor <= 0:
            raise ValueError(
                "Conversion factor must be greater than zero"
            )

    @property
    def source_code(self) -> str:
        return self.source_unit.code

    @property
    def target_code(self) -> str:
        return self.target_unit.code