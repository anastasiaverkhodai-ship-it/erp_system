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
from app.services.ukrainian_working_system_account_catalog_builder import (
    build_ukrainian_working_system_account_catalog,
)


class CompanyChartOfAccountsBackfillError(
    Exception
):
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
    Upgrade one company to its complete working
    Ukrainian Chart of Accounts.

    The operation:

    - preserves IDs of compatible existing accounts;
    - promotes compatible working accounts to system;
    - creates missing system accounts;
    - establishes working-account hierarchy;
    - applies required non-postable parent state;
    - preserves existing non-postable synthetic accounts
      when they may already have custom children;
    - is company-scoped and concurrency-safe;
    - does not commit.

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

    company = (
        company_result.scalar_one_or_none()
    )

    if company is None:
        raise (
            CompanyChartOfAccountsBackfillCompanyNotFoundError(
                "Company not found"
            )
        )

    catalog = (
        build_ukrainian_working_system_account_catalog(
            company.chart_of_accounts_template
        )
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

    # -----------------------------------------------------
    # Protect against unexpected SYSTEM accounts.
    #
    # Non-system custom accounts are allowed.
    # -----------------------------------------------------

    unexpected_system_codes = sorted(
        account.code
        for account in existing_accounts
        if (
            account.is_system
            and account.code
            not in expected_by_code
        )
    )

    if unexpected_system_codes:
        raise (
            CompanyChartOfAccountsBackfillConflictError(
                "Company contains system accounts "
                "outside its selected Chart of "
                "Accounts template: "
                f"{unexpected_system_codes}"
            )
        )

    promoted_count = 0
    created_count = 0

    # -----------------------------------------------------
    # First pass:
    #
    # - validate compatible existing accounts;
    # - promote compatible legacy working accounts;
    # - create missing accounts without parent_id.
    #
    # Parent IDs are assigned only after flush.
    # -----------------------------------------------------

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
                mismatches.append(
                    "account_type"
                )

            if (
                existing.normal_balance
                != definition.normal_balance
            ):
                mismatches.append(
                    "normal_balance"
                )

            # Synthetic/root accounts must never
            # themselves have a parent.
            if (
                definition.parent_code is None
                and existing.parent_id is not None
            ):
                mismatches.append(
                    "parent_id"
                )

            if mismatches:
                raise (
                    CompanyChartOfAccountsBackfillConflictError(
                        "Existing account conflicts "
                        "with the selected working "
                        "Chart of Accounts: "
                        f"code={definition.code}, "
                        f"fields={mismatches}"
                    )
                )

            if not existing.is_system:
                existing.is_system = True
                promoted_count += 1

            # Required system parents must become
            # non-postable.
            #
            # If a synthetic definition is postable,
            # preserve an existing False value because
            # the legacy company may already have its
            # own custom child accounts.
            if (
                not definition.is_postable
                and existing.is_postable
            ):
                existing.is_postable = False

            # Three-digit working accounts are leaf
            # system accounts in this architecture.
            if (
                len(definition.code) == 3
                and existing.is_postable
                != definition.is_postable
            ):
                existing.is_postable = (
                    definition.is_postable
                )

            continue

        account = Account(
            company_id=company_id,
            code=definition.code,
            name=definition.name,
            account_type=definition.account_type,
            normal_balance=(
                definition.normal_balance
            ),
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

    # Allocate IDs for all newly created accounts.
    await session.flush()

    # -----------------------------------------------------
    # Second pass:
    #
    # Establish and validate system hierarchy.
    # -----------------------------------------------------

    hierarchy_changed = False

    for definition in definitions:
        account = existing_by_code[
            definition.code
        ]

        if definition.parent_code is None:
            continue

        parent = existing_by_code[
            definition.parent_code
        ]

        if account.parent_id is None:
            account.parent_id = parent.id
            hierarchy_changed = True

        elif account.parent_id != parent.id:
            raise (
                CompanyChartOfAccountsBackfillConflictError(
                    "Existing working account has "
                    "an incorrect parent: "
                    f"code={definition.code}, "
                    f"expected_parent="
                    f"{definition.parent_code}"
                )
            )

        if parent.is_postable:
            parent.is_postable = False
            hierarchy_changed = True

        if (
            account.is_postable
            != definition.is_postable
        ):
            account.is_postable = (
                definition.is_postable
            )
            hierarchy_changed = True

    if hierarchy_changed:
        await session.flush()

    custom_count = sum(
        account.code not in expected_by_code
        for account in existing_accounts
    )

    return CompanyChartOfAccountsBackfillResult(
        company_id=company.id,
        template_type=(
            company.chart_of_accounts_template
        ),
        promoted_count=promoted_count,
        created_count=created_count,
        custom_count=custom_count,
    )
