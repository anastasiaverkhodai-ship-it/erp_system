from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)


class WorkingAccountRoleNotConfiguredError(
    KeyError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class ChartOfAccountsWorkingProfile:
    template_type: ChartOfAccountsTemplateType
    role_to_code: Mapping[
        AccountingAccountRole,
        str,
    ]

    def __post_init__(self) -> None:
        if not isinstance(
            self.template_type,
            ChartOfAccountsTemplateType,
        ):
            raise TypeError(
                "template_type must be "
                "ChartOfAccountsTemplateType"
            )

        normalized: dict[
            AccountingAccountRole,
            str,
        ] = {}

        for role, code in self.role_to_code.items():
            if not isinstance(
                role,
                AccountingAccountRole,
            ):
                raise TypeError(
                    "Working profile role must be "
                    "AccountingAccountRole"
                )

            if (
                len(code) not in (2, 3)
                or not code.isdigit()
            ):
                raise ValueError(
                    "Working account code must contain "
                    "2 or 3 numeric digits"
                )

            normalized[role] = code

        object.__setattr__(
            self,
            "role_to_code",
            MappingProxyType(normalized),
        )

    @property
    def count(self) -> int:
        return len(self.role_to_code)

    def get_code(
        self,
        role: AccountingAccountRole,
    ) -> str:
        code = self.get_code_or_none(role)

        if code is None:
            raise WorkingAccountRoleNotConfiguredError(
                (
                    f"Accounting role "
                    f"{role.value!r} is not configured "
                    f"for chart template "
                    f"{self.template_type.value!r}"
                )
            )

        return code

    def get_code_or_none(
        self,
        role: AccountingAccountRole,
    ) -> str | None:
        if not isinstance(
            role,
            AccountingAccountRole,
        ):
            raise TypeError(
                "role must be AccountingAccountRole"
            )

        return self.role_to_code.get(role)

    def configured_roles(
        self,
    ) -> tuple[AccountingAccountRole, ...]:
        return tuple(
            sorted(
                self.role_to_code,
                key=lambda role: role.value,
            )
        )
