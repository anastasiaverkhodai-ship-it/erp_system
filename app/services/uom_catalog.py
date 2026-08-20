from app.services.uom_definition import UnitOfMeasureDefinition
from app.services.uom_types import UnitDimension


PIECE = UnitOfMeasureDefinition(
    code="pcs",
    name="Piece",
    symbol="pcs",
    dimension=UnitDimension.COUNT,
    precision=0,
)

KILOGRAM = UnitOfMeasureDefinition(
    code="kg",
    name="Kilogram",
    symbol="kg",
    dimension=UnitDimension.MASS,
    precision=3,
)

GRAM = UnitOfMeasureDefinition(
    code="g",
    name="Gram",
    symbol="g",
    dimension=UnitDimension.MASS,
    precision=3,
)

LITRE = UnitOfMeasureDefinition(
    code="l",
    name="Litre",
    symbol="l",
    dimension=UnitDimension.VOLUME,
    precision=3,
)

MILLILITRE = UnitOfMeasureDefinition(
    code="ml",
    name="Millilitre",
    symbol="ml",
    dimension=UnitDimension.VOLUME,
    precision=3,
)

METRE = UnitOfMeasureDefinition(
    code="m",
    name="Metre",
    symbol="m",
    dimension=UnitDimension.LENGTH,
    precision=3,
)

CENTIMETRE = UnitOfMeasureDefinition(
    code="cm",
    name="Centimetre",
    symbol="cm",
    dimension=UnitDimension.LENGTH,
    precision=3,
)


SYSTEM_UNITS: tuple[UnitOfMeasureDefinition, ...] = (
    PIECE,
    KILOGRAM,
    GRAM,
    LITRE,
    MILLILITRE,
    METRE,
    CENTIMETRE,
)