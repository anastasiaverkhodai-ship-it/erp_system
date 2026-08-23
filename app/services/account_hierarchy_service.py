from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account


class AccountHierarchyError(Exception):
    pass


class AccountParentNotFoundError(
    AccountHierarchyError
):
    pass


class AccountHierarchyCycleError(
    AccountHierarchyError
):
    pass


async def lock_account_hierarchy(
    *,
    session: AsyncSession,
    company_id: int,
) -> None:
    """
    Serialize hierarchy mutations inside one company.

    The PostgreSQL advisory lock is transaction-scoped,
    so it is automatically released on commit/rollback.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be positive"
        )

    scope = f"account-hierarchy:{company_id}"

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:scope, 0)"
            ")"
        ),
        {
            "scope": scope,
        },
    )


async def validate_account_parent(
    *,
    session: AsyncSession,
    company_id: int,
    account_id: int | None,
    parent_id: int | None,
) -> Account | None:
    """
    Validate one proposed parent relationship.

    The caller should acquire lock_account_hierarchy()
    before calling this function when changing hierarchy.

    Returns the direct parent Account when parent_id
    is provided, otherwise None.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be positive"
        )

    if account_id is not None and account_id <= 0:
        raise ValueError(
            "account_id must be positive"
        )

    if parent_id is None:
        return None

    if parent_id <= 0:
        raise ValueError(
            "parent_id must be positive"
        )

    if (
        account_id is not None
        and parent_id == account_id
    ):
        raise AccountHierarchyCycleError(
            "Account cannot be its own parent"
        )

    current_id: int | None = parent_id
    visited_ids: set[int] = set()
    direct_parent: Account | None = None

    while current_id is not None:
        if current_id in visited_ids:
            raise AccountHierarchyCycleError(
                "Existing account hierarchy contains a cycle"
            )

        visited_ids.add(current_id)

        result = await session.execute(
            select(Account).where(
                Account.id == current_id,
                Account.company_id == company_id,
            )
        )

        current = result.scalar_one_or_none()

        if current is None:
            raise AccountParentNotFoundError(
                "Parent account not found in company"
            )

        if direct_parent is None:
            direct_parent = current

        if (
            account_id is not None
            and current.id == account_id
        ):
            raise AccountHierarchyCycleError(
                "Account hierarchy cycle detected"
            )

        current_id = current.parent_id

    return direct_parent
