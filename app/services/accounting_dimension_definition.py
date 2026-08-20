from dataclasses import dataclass

from app.services.accounting_dimension_types import (
    AccountingDimensionType,
)


@dataclass(frozen=True, slots=True)
class AccountingDimensionDefinition:
    """
    Immutable metadata describing an accounting dimension.

    code
        Stable internal system identifier.

    name
        Human-readable dimension name.

    dimension_type
        High-level semantic type of the dimension.

    required
        Whether the dimension must be present
        when it is assigned to an accounting account.
    """

    code: str
    name: str
    dimension_type: AccountingDimensionType
    required: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError(
                "Accounting dimension code cannot be empty"
            )

        if not self.name.strip():
            raise ValueError(
                "Accounting dimension name cannot be empty"
            )