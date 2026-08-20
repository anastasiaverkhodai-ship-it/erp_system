from app.services.account_dimension_catalog import (
    AccountDimensionCatalog,
)
from app.services.accounting_dimension_value import (
    AccountingDimensionValue,
)


class AccountingDimensionValidationError(Exception):
    """Base error for accounting dimension value validation."""


class MissingRequiredAccountingDimensionError(
    AccountingDimensionValidationError
):
    """Raised when a required account dimension value is missing."""


class UnexpectedAccountingDimensionError(
    AccountingDimensionValidationError
):
    """Raised when a dimension is not assigned to the account."""


class DuplicateAccountingDimensionValueError(
    AccountingDimensionValidationError
):
    """Raised when the same dimension value is provided twice."""


def validate_account_dimension_values(
    account_id: int,
    values: tuple[AccountingDimensionValue, ...],
    catalog: AccountDimensionCatalog,
) -> None:
    """
    Validate accounting dimension values for an account.
    """

    if account_id <= 0:
        raise ValueError(
            "Account ID must be greater than zero"
        )

    assignments = catalog.for_account(
        account_id
    )

    assignments_by_code = {
        assignment.dimension_code: assignment
        for assignment in assignments
    }

    values_by_code: dict[
        str,
        AccountingDimensionValue,
    ] = {}

    for value in values:
        if value.dimension_code in values_by_code:
            raise DuplicateAccountingDimensionValueError(
                "Duplicate accounting dimension value: "
                f"account_id={account_id}, "
                f"dimension='{value.dimension_code}'"
            )

        if value.dimension_code not in assignments_by_code:
            raise UnexpectedAccountingDimensionError(
                "Unexpected accounting dimension: "
                f"account_id={account_id}, "
                f"dimension='{value.dimension_code}'"
            )

        values_by_code[value.dimension_code] = value

    missing_required = [
        assignment.dimension_code
        for assignment in assignments
        if (
            assignment.required
            and assignment.dimension_code
            not in values_by_code
        )
    ]

    if missing_required:
        raise MissingRequiredAccountingDimensionError(
            "Missing required accounting dimensions: "
            + ", ".join(missing_required)
        )