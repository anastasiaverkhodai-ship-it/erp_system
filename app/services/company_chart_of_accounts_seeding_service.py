from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.company import Company
from app.services.account_hierarchy_service import (
    lock_account_hierarchy,
)
from app.services.ukrainian_working_system_account_catalog_builder import (
    build_ukrainian_working_system_account_catalog,
)


class CompanyChartOfAccountsSeedingError(Exception):
    pass


class CompanyChartOfAccountsCompanyNotFoundError(
    CompanyChartOfAccountsSeedingError
):
    pass


class CompanyChartOfAccountsConflictError(
    CompanyChartOfAccountsSeedingError
):
    pass


class CompanyChartOfAccountsMismatchError(
    CompanyChartOfAccountsSeedingError
):
    pass


async def seed_company_chart_of_accounts(
    *,
    session: AsyncSession,
    company_id: int,
) -> tuple[
    Account,
    ...,
]:
    """
    Seed the selected Ukrainian system Chart of Accounts
    for one company.

    The operation is:
    - company-scoped;
    - concurrency-safe;
    - idempotent;
    - template-aware;
    - non-committing.

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
        raise CompanyChartOfAccountsCompanyNotFoundError(
            "Company not found"
        )

    catalog = build_ukrainian_working_system_account_catalog(
        company.chart_of_accounts_template
    )

    definitions = catalog.seed_order()

    expected_codes = {
        definition.code
        for definition in definitions
    }

    # -----------------------------------------------------
    # Idempotency / template mismatch protection
    # -----------------------------------------------------

    existing_system_result = await session.execute(
        select(Account)
        .where(
            Account.company_id == company_id,
            Account.is_system.is_(True),
        )
        .order_by(Account.code)
    )

    existing_system_accounts = tuple(
        existing_system_result.scalars().all()
    )

    if existing_system_accounts:
        existing_system_codes = {
            account.code
            for account in existing_system_accounts
        }

        if existing_system_codes != expected_codes:
            missing = (
                expected_codes
                - existing_system_codes
            )

            unexpected = (
                existing_system_codes
                - expected_codes
            )

            raise CompanyChartOfAccountsMismatchError(
                "Existing system Chart of Accounts does "
                "not match the company's selected "
                "template. "
                f"Missing: {sorted(missing)}. "
                f"Unexpected: {sorted(unexpected)}."
            )

        # Already seeded correctly.
        return existing_system_accounts

    # -----------------------------------------------------
    # Protect user-created accounts from code collisions
    # -----------------------------------------------------

    conflicting_result = await session.execute(
        select(Account)
        .where(
            Account.company_id == company_id,
            Account.code.in_(expected_codes),
        )
        .order_by(Account.code)
    )

    conflicting_accounts = tuple(
        conflicting_result.scalars().all()
    )

    if conflicting_accounts:
        conflicting_codes = sorted(
            {
                account.code
                for account in conflicting_accounts
            }
        )

        raise CompanyChartOfAccountsConflictError(
            "Cannot seed system Chart of Accounts "
            "because company accounts already use "
            "official system codes: "
            f"{conflicting_codes}"
        )

    # -----------------------------------------------------
    # Create system accounts.
    #
    # First pass inserts all definitions without parents.
    # This is intentionally future-proof for 3-digit
    # subaccounts: after flush every parent has an ID.
    # -----------------------------------------------------

    created_by_code: dict[
        str,
        Account,
    ] = {}

    created_accounts: list[
        Account
    ] = []

    for definition in definitions:
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

        created_by_code[
            definition.code
        ] = account

        created_accounts.append(account)

    # Allocate database IDs.
    await session.flush()

    # -----------------------------------------------------
    # Second pass establishes hierarchy when subaccounts
    # are added to the catalog in later stages.
    # -----------------------------------------------------

    hierarchy_changed = False

    for definition in definitions:
        if definition.parent_code is None:
            continue

        account = created_by_code[
            definition.code
        ]

        parent = created_by_code[
            definition.parent_code
        ]

        account.parent_id = parent.id

        hierarchy_changed = True

    if hierarchy_changed:
        await session.flush()

    return tuple(created_accounts)
