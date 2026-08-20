from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccountDimensionAssignment:
    """
    Assigns an accounting dimension to an account.

    account_id
        Accounting account that uses the dimension.

    dimension_code
        Stable accounting dimension code.

    required
        Whether the dimension value is mandatory
        for journal lines posted to this account.

    position
        Stable display and validation order
        of dimensions assigned to the account.
    """

    account_id: int
    dimension_code: str
    required: bool
    position: int

    def __post_init__(self) -> None:
        if self.account_id <= 0:
            raise ValueError(
                "Account ID must be greater than zero"
            )

        if not self.dimension_code.strip():
            raise ValueError(
                "Accounting dimension code cannot be empty"
            )

        if self.position <= 0:
            raise ValueError(
                "Accounting dimension position "
                "must be greater than zero"
            )