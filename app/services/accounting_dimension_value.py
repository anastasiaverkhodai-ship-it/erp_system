from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccountingDimensionValue:
    """
    Concrete accounting dimension value.

    dimension_code
        Stable accounting dimension code.

    entity_id
        ID of the concrete entity used as the subconto value.

    Example:

        dimension_code = "product"
        entity_id = 15

        means:
        Product with ID 15 is the value of the product dimension.
    """

    dimension_code: str
    entity_id: int

    def __post_init__(self) -> None:
        if not self.dimension_code.strip():
            raise ValueError(
                "Accounting dimension code cannot be empty"
            )

        if self.entity_id <= 0:
            raise ValueError(
                "Accounting dimension entity ID "
                "must be greater than zero"
            )