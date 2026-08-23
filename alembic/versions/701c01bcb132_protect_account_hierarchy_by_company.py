"""protect account hierarchy by company

Revision ID: 701c01bcb132
Revises: 727a8ac46f9d
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "701c01bcb132"
down_revision: Union[str, Sequence[str], None] = (
    "727a8ac46f9d"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COMPANY_ACCOUNT_UNIQUE = (
    "uq_accounts_company_id_id"
)

COMPANY_PARENT_FK = (
    "fk_accounts_company_parent"
)

OLD_PARENT_FK = (
    "accounts_parent_id_fkey"
)


def upgrade() -> None:
    """
    Enforce company-safe account hierarchy.

    A child account can reference a parent only when
    both accounts belong to the same company.
    """

    # PostgreSQL requires the referenced composite
    # key to be UNIQUE.
    op.create_unique_constraint(
        COMPANY_ACCOUNT_UNIQUE,
        "accounts",
        [
            "company_id",
            "id",
        ],
    )

    # Remove the old FK that validated only parent_id.
    op.drop_constraint(
        OLD_PARENT_FK,
        "accounts",
        type_="foreignkey",
    )

    # Replace it with a company-aware composite FK.
    op.create_foreign_key(
        COMPANY_PARENT_FK,
        source_table="accounts",
        referent_table="accounts",
        local_cols=[
            "company_id",
            "parent_id",
        ],
        remote_cols=[
            "company_id",
            "id",
        ],
    )


def downgrade() -> None:
    """
    Restore the previous parent_id-only FK.
    """

    op.drop_constraint(
        COMPANY_PARENT_FK,
        "accounts",
        type_="foreignkey",
    )

    op.create_foreign_key(
        OLD_PARENT_FK,
        source_table="accounts",
        referent_table="accounts",
        local_cols=[
            "parent_id",
        ],
        remote_cols=[
            "id",
        ],
    )

    op.drop_constraint(
        COMPANY_ACCOUNT_UNIQUE,
        "accounts",
        type_="unique",
    )
