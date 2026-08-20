from decimal import Decimal

from app.services.uom_catalog import (
    CENTIMETRE,
    GRAM,
    KILOGRAM,
    LITRE,
    METRE,
    MILLILITRE,
)
from app.services.uom_conversion_definition import (
    UnitConversionDefinition,
)


KILOGRAM_TO_GRAM = UnitConversionDefinition(
    source_unit=KILOGRAM,
    target_unit=GRAM,
    factor=Decimal("1000"),
)

LITRE_TO_MILLILITRE = UnitConversionDefinition(
    source_unit=LITRE,
    target_unit=MILLILITRE,
    factor=Decimal("1000"),
)

METRE_TO_CENTIMETRE = UnitConversionDefinition(
    source_unit=METRE,
    target_unit=CENTIMETRE,
    factor=Decimal("100"),
)


SYSTEM_UNIT_CONVERSIONS: tuple[
    UnitConversionDefinition,
    ...,
] = (
    KILOGRAM_TO_GRAM,
    LITRE_TO_MILLILITRE,
    METRE_TO_CENTIMETRE,
)