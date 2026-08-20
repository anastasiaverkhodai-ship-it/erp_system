from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ProductUnitConversionDefinition:
    """
    Product-specific unit conversion.

    factor means:

        target_quantity = source_quantity * factor

    Example:

        product_id = 10
        box -> pcs
        factor = 12

        1 box = 12 pcs
    """

    product_id: int
    source_code: str
    target_code: str
    factor: Decimal

    def __post_init__(self) -> None:
        if self.product_id <= 0:
            raise ValueError(
                "Product ID must be greater than zero"
            )

        if not self.source_code.strip():
            raise ValueError(
                "Source unit code cannot be empty"
            )

        if not self.target_code.strip():
            raise ValueError(
                "Target unit code cannot be empty"
            )

        if self.source_code == self.target_code:
            raise ValueError(
                "Source and target units must be different"
            )

        if self.factor <= 0:
            raise ValueError(
                "Conversion factor must be greater than zero"
            )