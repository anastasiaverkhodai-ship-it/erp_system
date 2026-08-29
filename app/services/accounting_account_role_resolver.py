from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.company import Company
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.ukrainian_chart_working_profiles import (
    get_ukrainian_chart_working_profile,
)


class AccountingAccountRoleResolutionError(
    Exception
):
    pass


class AccountingCompanyNotFoundError(
    AccountingAccountRoleResolutionError
):
    pass


class AccountingRoleNotConfiguredError(
    AccountingAccountRoleResolutionError
):
    pass


class AccountingRoleAccountInvalidError(
    AccountingAccountRoleResolutionError
):
    pass


async def resolve_company_account_roles(
    db: AsyncSession,
    *,
    company_id: int,
    roles: Iterable[
        AccountingAccountRole
    ],
) -> dict[
    AccountingAccountRole,
    Account,
]:
    requested_roles = tuple(
        dict.fromkeys(
            roles
        )
    )

    if not requested_roles:
        return {}

    company = (
        await db.execute(
            select(
                Company
            ).where(
                Company.id
                == company_id,
                Company.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if company is None:
        raise AccountingCompanyNotFoundError(
            "Active company not found"
        )

    profile = (
        get_ukrainian_chart_working_profile(
            company.chart_of_accounts_template
        )
    )

    role_to_code: dict[
        AccountingAccountRole,
        str,
    ] = {}

    missing_roles: list[
        AccountingAccountRole
    ] = []

    for role in requested_roles:
        code = (
            profile.get_code_or_none(
                role
            )
        )

        if code is None:
            missing_roles.append(
                role
            )
            continue

        role_to_code[
            role
        ] = code

    if missing_roles:
        raise AccountingRoleNotConfiguredError(
            "Accounting account roles are not "
            "configured for company chart "
            f"template "
            f"{company.chart_of_accounts_template.value}: "
            + ", ".join(
                role.value
                for role
                in missing_roles
            )
        )

    codes = tuple(
        dict.fromkeys(
            role_to_code.values()
        )
    )

    accounts = tuple(
        (
            await db.execute(
                select(
                    Account
                ).where(
                    Account.company_id
                    == company_id,
                    Account.code.in_(
                        codes
                    ),
                )
            )
        ).scalars().all()
    )

    account_by_code = {
        account.code: account
        for account
        in accounts
    }

    resolved: dict[
        AccountingAccountRole,
        Account,
    ] = {}

    for role, code in role_to_code.items():
        account = (
            account_by_code.get(
                code
            )
        )

        if account is None:
            raise AccountingRoleAccountInvalidError(
                f"Accounting account {code} "
                f"for role {role.value} "
                "does not exist"
            )

        if not account.is_system:
            raise AccountingRoleAccountInvalidError(
                f"Accounting account {code} "
                f"for role {role.value} "
                "is not a system account"
            )

        if not account.is_active:
            raise AccountingRoleAccountInvalidError(
                f"Accounting account {code} "
                f"for role {role.value} "
                "is inactive"
            )

        if not account.is_postable:
            raise AccountingRoleAccountInvalidError(
                f"Accounting account {code} "
                f"for role {role.value} "
                "is not postable"
            )

        resolved[
            role
        ] = account

    return resolved
