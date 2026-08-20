from decimal import Decimal

from app.services.uom_catalog_service import (
    SYSTEM_UOM_CATALOG,
)
from app.services.uom_conversion_catalog import (
    SYSTEM_UNIT_CONVERSION_CATALOG,
)


def convert_quantity(
    quantity: Decimal,
    source_code: str,
    target_code: str,
) -> Decimal:
    """
    Convert a quantity from one unit of measure to another.

    The function preserves Decimal precision and does not
    round the result. Rounding rules belong to a higher
    business layer.
    """

    source_unit = SYSTEM_UOM_CATALOG.get(
        source_code
    )
    target_unit = SYSTEM_UOM_CATALOG.get(
        target_code
    )

    if source_unit.code == target_unit.code:
        return quantity

    if source_unit.dimension != target_unit.dimension:
        raise ValueError(
            "Cannot convert units with different dimensions: "
            f"'{source_code}' -> '{target_code}'"
        )

    factor = SYSTEM_UNIT_CONVERSION_CATALOG.get_factor(
        source_code=source_code,
        target_code=target_code,
    )

    return quantity * factor