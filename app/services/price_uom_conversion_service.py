from decimal import Decimal

from app.services.product_uom_conversion_catalog import (
    ProductUnitConversionCatalog,
)
from app.services.product_uom_conversion_service import (
    convert_product_quantity,
)


def convert_product_price_uom(
    amount: Decimal,
    product_id: int,
    source_uom_code: str,
    target_uom_code: str,
    catalog: ProductUnitConversionCatalog,
) -> Decimal:
    """
    Convert a product price from one UOM to another.

    Example:

        price = 10 UAH / pcs
        1 box = 12 pcs

        pcs -> box
        price = 120 UAH / box
    """

    if amount < 0:
        raise ValueError(
            "Price amount cannot be negative"
        )

    if product_id <= 0:
        raise ValueError(
            "Product ID must be greater than zero"
        )

    if not source_uom_code.strip():
        raise ValueError(
            "Source UOM code cannot be empty"
        )

    if not target_uom_code.strip():
        raise ValueError(
            "Target UOM code cannot be empty"
        )

    if source_uom_code == target_uom_code:
        return amount

    source_units_per_target_unit = (
        convert_product_quantity(
            quantity=Decimal("1"),
            product_id=product_id,
            source_code=target_uom_code,
            target_code=source_uom_code,
            catalog=catalog,
        )
    )

    return amount * source_units_per_target_unit