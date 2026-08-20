from app.services.account_dimension_assignment import (
    AccountDimensionAssignment,
)


class AccountDimensionCatalogError(Exception):
    """Base error for account dimension catalog operations."""


class DuplicateAccountDimensionError(
    AccountDimensionCatalogError
):
    """Raised when the same dimension is assigned twice to an account."""


class DuplicateAccountDimensionPositionError(
    AccountDimensionCatalogError
):
    """Raised when two dimensions use the same position on an account."""


class AccountDimensionCatalog:
    def __init__(
        self,
        assignments: tuple[
            AccountDimensionAssignment,
            ...,
        ],
    ) -> None:
        self._assignments: dict[
            tuple[int, str],
            AccountDimensionAssignment,
        ] = {}

        self._positions: set[
            tuple[int, int]
        ] = set()

        for assignment in assignments:
            dimension_key = (
                assignment.account_id,
                assignment.dimension_code,
            )

            position_key = (
                assignment.account_id,
                assignment.position,
            )

            if dimension_key in self._assignments:
                raise DuplicateAccountDimensionError(
                    "Duplicate accounting dimension assignment: "
                    f"account_id={assignment.account_id}, "
                    f"dimension='{assignment.dimension_code}'"
                )

            if position_key in self._positions:
                raise DuplicateAccountDimensionPositionError(
                    "Duplicate accounting dimension position: "
                    f"account_id={assignment.account_id}, "
                    f"position={assignment.position}"
                )

            self._assignments[dimension_key] = assignment
            self._positions.add(position_key)

    def for_account(
        self,
        account_id: int,
    ) -> tuple[AccountDimensionAssignment, ...]:
        return tuple(
            sorted(
                (
                    assignment
                    for assignment
                    in self._assignments.values()
                    if assignment.account_id == account_id
                ),
                key=lambda assignment: assignment.position,
            )
        )

    def all(
        self,
    ) -> tuple[AccountDimensionAssignment, ...]:
        return tuple(self._assignments.values())