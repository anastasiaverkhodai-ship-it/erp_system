from decimal import Decimal

from app.services.product_uom_conversion_catalog import (
    ProductUnitConversionCatalog,
)


def convert_product_quantity(
    quantity: Decimal,
    product_id: int,
    source_code: str,
    target_code: str,
    catalog: ProductUnitConversionCatalog,
) -> Decimal:
    """
    Convert a product quantity using a product-specific
    unit conversion.

    Example:

        product_id = 10
        box -> pcs
        factor = 12

        2 box -> 24 pcs
    """

    if product_id <= 0:
        raise ValueError(
            "Product ID must be greater than zero"
        )

    if not source_code.strip():
        raise ValueError(
            "Source unit code cannot be empty"
        )

    if not target_code.strip():
        raise ValueError(
            "Target unit code cannot be empty"
        )

    if source_code == target_code:
        return quantity

    factor = catalog.get_factor(
        product_id=product_id,
        source_code=source_code,
        target_code=target_code,
    )

    return quantity * factor