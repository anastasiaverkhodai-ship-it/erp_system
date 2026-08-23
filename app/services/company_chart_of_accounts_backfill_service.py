from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.company import Company
from app.services.account_hierarchy_service import (
    lock_account_hierarchy,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_system_account_catalog_builder import (
    build_ukrainian_system_account_catalog,
)


class CompanyChartOfAccountsBackfillError(Exception):
    pass


class CompanyChartOfAccountsBackfillCompanyNotFoundError(
    CompanyChartOfAccountsBackfillError
):
    pass


class CompanyChartOfAccountsBackfillConflictError(
    CompanyChartOfAccountsBackfillError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class CompanyChartOfAccountsBackfillResult:
    company_id: int
    template_type: ChartOfAccountsTemplateType
    promoted_count: int
    created_count: int
    custom_count: int


async def backfill_company_chart_of_accounts(
    *,
    session: AsyncSession,
    company_id: int,
) -> CompanyChartOfAccountsBackfillResult:
    """
    Backfill the selected system Chart of Accounts for
    a legacy company.

    Compatible existing official synthetic accounts keep
    their database IDs and are promoted to is_system=True.

    Missing official synthetic accounts are created.

    Custom accounts are preserved unchanged.

    Existing is_postable values are preserved because a
    legacy synthetic account may already have custom
    subaccounts and therefore correctly be non-postable.

    The caller owns commit/rollback.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be positive"
        )

    await lock_account_hierarchy(
        session=session,
        company_id=company_id,
    )

    company_result = await session.execute(
        select(Company)
        .where(
            Company.id == company_id
        )
        .with_for_update()
    )

    company = company_result.scalar_one_or_none()

    if company is None:
        raise (
            CompanyChartOfAccountsBackfillCompanyNotFoundError(
                "Company not found"
            )
        )

    catalog = build_ukrainian_system_account_catalog(
        company.chart_of_accounts_template
    )

    definitions = catalog.seed_order()

    expected_by_code = {
        definition.code: definition
        for definition in definitions
    }

    existing_result = await session.execute(
        select(Account)
        .where(
            Account.company_id == company_id
        )
        .order_by(Account.code)
    )

    existing_accounts = tuple(
        existing_result.scalars().all()
    )

    existing_by_code = {
        account.code: account
        for account in existing_accounts
    }

    promoted_count = 0
    created_count = 0

    for definition in definitions:
        existing = existing_by_code.get(
            definition.code
        )

        if existing is not None:
            mismatches: list[str] = []

            if existing.name != definition.name:
                mismatches.append("name")

            if (
                existing.account_type
                != definition.account_type
            ):
                mismatches.append("account_type")

            if (
                existing.normal_balance
                != definition.normal_balance
            ):
                mismatches.append("normal_balance")

            if existing.parent_id is not None:
                mismatches.append("parent_id")

            if mismatches:
                raise CompanyChartOfAccountsBackfillConflictError(
                    "Existing account conflicts with "
                    "the selected system Chart of Accounts: "
                    f"code={definition.code}, "
                    f"fields={mismatches}"
                )

            if not existing.is_system:
                existing.is_system = True
                promoted_count += 1

            continue

        account = Account(
            company_id=company_id,
            code=definition.code,
            name=definition.name,
            account_type=definition.account_type,
            normal_balance=definition.normal_balance,
            parent_id=None,
            is_postable=definition.is_postable,
            is_system=True,
            is_active=True,
        )

        session.add(account)

        existing_by_code[
            definition.code
        ] = account

        created_count += 1

    await session.flush()

    custom_count = sum(
        account.code not in expected_by_code
        for account in existing_accounts
    )

    return CompanyChartOfAccountsBackfillResult(
        company_id=company.id,
        template_type=company.chart_of_accounts_template,
        promoted_count=promoted_count,
        created_count=created_count,
        custom_count=custom_count,
    )
