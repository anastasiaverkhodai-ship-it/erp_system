from app.services.system_account_definition import (
    SystemAccountDefinition,
)


class SystemAccountCatalogError(Exception):
    pass


class DuplicateSystemAccountCodeError(
    SystemAccountCatalogError
):
    pass


class SystemAccountNotFoundError(
    SystemAccountCatalogError
):
    pass


class MissingSystemAccountParentError(
    SystemAccountCatalogError
):
    pass


class InvalidSystemAccountPostabilityError(
    SystemAccountCatalogError
):
    pass


class SystemAccountCatalog:
    """
    Immutable validated catalog of system Chart of
    Accounts definitions.

    Validation happens before any database seeding.
    """

    def __init__(
        self,
        accounts: tuple[
            SystemAccountDefinition,
            ...,
        ],
    ) -> None:
        if not accounts:
            raise ValueError(
                "System account catalog cannot be empty"
            )

        by_code: dict[
            str,
            SystemAccountDefinition,
        ] = {}

        for account in accounts:
            if account.code in by_code:
                raise DuplicateSystemAccountCodeError(
                    "Duplicate system account code: "
                    f"{account.code}"
                )

            by_code[account.code] = account

        # Every 3-digit system subaccount must have
        # an existing 2-digit parent.
        for account in accounts:
            if len(account.code) == 2:
                continue

            if account.parent_code is None:
                raise MissingSystemAccountParentError(
                    "System subaccount must define "
                    "a parent account: "
                    f"{account.code}"
                )

            parent = by_code.get(
                account.parent_code
            )

            if parent is None:
                raise MissingSystemAccountParentError(
                    "Parent system account "
                    f"{account.parent_code} "
                    "does not exist for "
                    f"subaccount {account.code}"
                )

            if len(parent.code) != 2:
                raise MissingSystemAccountParentError(
                    "System subaccount parent must "
                    "be a 2-digit account: "
                    f"{account.code}"
                )

        # Any account that has children acts as a
        # grouping/synthetic account in this ERP and
        # therefore cannot accept direct postings.
        parent_codes = {
            account.parent_code
            for account in accounts
            if account.parent_code is not None
        }

        for parent_code in parent_codes:
            parent = by_code[parent_code]

            if parent.is_postable:
                raise InvalidSystemAccountPostabilityError(
                    "System account with subaccounts "
                    "cannot be postable: "
                    f"{parent.code}"
                )

        self._accounts = accounts
        self._by_code = by_code

    @property
    def count(self) -> int:
        return len(self._accounts)

    def get(
        self,
        code: str,
    ) -> SystemAccountDefinition:
        try:
            return self._by_code[code]
        except KeyError as exc:
            raise SystemAccountNotFoundError(
                "System account not found: "
                f"{code}"
            ) from exc

    def get_or_none(
        self,
        code: str,
    ) -> SystemAccountDefinition | None:
        return self._by_code.get(code)

    def all(
        self,
    ) -> tuple[
        SystemAccountDefinition,
        ...,
    ]:
        return self._accounts

    def roots(
        self,
    ) -> tuple[
        SystemAccountDefinition,
        ...,
    ]:
        return tuple(
            sorted(
                (
                    account
                    for account in self._accounts
                    if account.parent_code is None
                ),
                key=lambda account: account.code,
            )
        )

    def children_of(
        self,
        parent_code: str,
    ) -> tuple[
        SystemAccountDefinition,
        ...,
    ]:
        if parent_code not in self._by_code:
            raise SystemAccountNotFoundError(
                "System account not found: "
                f"{parent_code}"
            )

        return tuple(
            sorted(
                (
                    account
                    for account in self._accounts
                    if account.parent_code
                    == parent_code
                ),
                key=lambda account: account.code,
            )
        )

    def seed_order(
        self,
    ) -> tuple[
        SystemAccountDefinition,
        ...,
    ]:
        """
        Deterministic parent-before-child order for
        database seeding.
        """

        return tuple(
            sorted(
                self._accounts,
                key=lambda account: (
                    len(account.code),
                    account.code,
                ),
            )
        )
