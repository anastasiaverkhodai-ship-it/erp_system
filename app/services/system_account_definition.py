from dataclasses import dataclass

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)


@dataclass(
    frozen=True,
    slots=True,
)
class SystemAccountDefinition:
    """
    Immutable definition of one account in the
    Ukrainian system Chart of Accounts template.

    Official account code and name come from the
    Ukrainian Chart of Accounts.

    account_type, normal_balance and is_postable are
    ERP metadata used by our accounting engine.
    """

    code: str
    name: str

    account_type: AccountType
    normal_balance: AccountNormalBalance

    parent_code: str | None = None
    is_postable: bool = True

    def __post_init__(self) -> None:
        normalized_code = self.code.strip()
        normalized_name = self.name.strip()

        if not normalized_code:
            raise ValueError(
                "System account code cannot be empty"
            )

        if normalized_code != self.code:
            raise ValueError(
                "System account code cannot contain "
                "leading or trailing whitespace"
            )

        if not normalized_code.isdigit():
            raise ValueError(
                "System account code must be numeric"
            )

        if len(normalized_code) not in {
            2,
            3,
        }:
            raise ValueError(
                "System account code must contain "
                "2 or 3 digits"
            )

        if not normalized_name:
            raise ValueError(
                "System account name cannot be empty"
            )

        if normalized_name != self.name:
            raise ValueError(
                "System account name cannot contain "
                "leading or trailing whitespace"
            )

        if len(normalized_name) > 255:
            raise ValueError(
                "System account name cannot exceed "
                "255 characters"
            )

        if not isinstance(
            self.account_type,
            AccountType,
        ):
            raise TypeError(
                "account_type must be AccountType"
            )

        if not isinstance(
            self.normal_balance,
            AccountNormalBalance,
        ):
            raise TypeError(
                "normal_balance must be "
                "AccountNormalBalance"
            )

        if self.parent_code is None:
            return

        normalized_parent = (
            self.parent_code.strip()
        )

        if normalized_parent != self.parent_code:
            raise ValueError(
                "Parent account code cannot contain "
                "leading or trailing whitespace"
            )

        if (
            not normalized_parent.isdigit()
            or len(normalized_parent) != 2
        ):
            raise ValueError(
                "Parent system account code must "
                "contain exactly 2 digits"
            )

        if len(normalized_code) != 3:
            raise ValueError(
                "Only a 3-digit subaccount may have "
                "a parent system account"
            )

        if not normalized_code.startswith(
            normalized_parent
        ):
            raise ValueError(
                "Subaccount code must start with "
                "its parent account code"
            )
