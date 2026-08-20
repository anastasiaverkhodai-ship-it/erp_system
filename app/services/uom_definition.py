from dataclasses import dataclass

from app.services.uom_types import UnitDimension


@dataclass(frozen=True, slots=True)
class UnitOfMeasureDefinition:
    """
    Immutable metadata describing a unit of measure.

    code
        Stable internal system identifier.

    name
        Human-readable unit name.

    symbol
        Short unit symbol used in UI and documents.

    dimension
        Physical or logical dimension of the unit.

    precision
        Number of decimal places allowed for quantities
        expressed in this unit.
    """

    code: str
    name: str
    symbol: str
    dimension: UnitDimension
    precision: int

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError(
                "Unit of measure code cannot be empty"
            )

        if not self.name.strip():
            raise ValueError(
                "Unit of measure name cannot be empty"
            )

        if not self.symbol.strip():
            raise ValueError(
                "Unit of measure symbol cannot be empty"
            )

        if self.precision < 0:
            raise ValueError(
                "Unit of measure precision cannot be negative"
            )