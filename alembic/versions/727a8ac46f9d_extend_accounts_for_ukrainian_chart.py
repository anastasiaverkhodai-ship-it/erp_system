"""extend accounts for ukrainian chart

Revision ID: 727a8ac46f9d
Revises: 61b4a1c94429
Create Date: 2026-08-22 18:16:35.245855
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "727a8ac46f9d"
down_revision: Union[str, Sequence[str], None] = (
    "61b4a1c94429"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACCOUNT_TYPE_CONSTRAINT = "account_type_enum"
NORMAL_BALANCE_CONSTRAINT = (
    "account_normal_balance_enum"
)


def upgrade() -> None:
    """
    Extend the existing accounts table without
    replacing any account rows or changing IDs.
    """

    # Existing account_type is VARCHAR(50).
    # Restrict it to the supported classification
    # values without replacing the column.
    op.create_check_constraint(
        ACCOUNT_TYPE_CONSTRAINT,
        "accounts",
        (
            "account_type IN ("
            "'asset', "
            "'liability', "
            "'equity', "
            "'income', "
            "'expense', "
            "'off_balance'"
            ")"
        ),
    )

    # -------------------------------------------------
    # normal_balance
    # -------------------------------------------------

    # Add nullable first so existing rows remain valid
    # while they are being backfilled.
    op.add_column(
        "accounts",
        sa.Column(
            "normal_balance",
            sa.String(length=20),
            nullable=True,
        ),
    )

    # Safe backfill for the currently supported
    # financial account classifications.
    op.execute(
        """
        UPDATE accounts
        SET normal_balance =
            CASE
                WHEN account_type IN (
                    'asset',
                    'expense'
                )
                    THEN 'debit'

                WHEN account_type IN (
                    'liability',
                    'equity',
                    'income'
                )
                    THEN 'credit'

                WHEN account_type = 'off_balance'
                    THEN 'debit_credit'

                ELSE NULL
            END
        """
    )

    op.create_check_constraint(
        NORMAL_BALANCE_CONSTRAINT,
        "accounts",
        (
            "normal_balance IN ("
            "'debit', "
            "'credit', "
            "'debit_credit'"
            ")"
        ),
    )

    op.alter_column(
        "accounts",
        "normal_balance",
        existing_type=sa.String(length=20),
        nullable=False,
    )

    # -------------------------------------------------
    # is_postable
    # -------------------------------------------------

    op.add_column(
        "accounts",
        sa.Column(
            "is_postable",
            sa.Boolean(),
            nullable=True,
        ),
    )

    # Existing parent/group accounts become
    # non-postable. Leaf accounts remain postable.
    op.execute(
        """
        UPDATE accounts AS account
        SET is_postable =
            NOT EXISTS (
                SELECT 1
                FROM accounts AS child
                WHERE child.parent_id = account.id
            )
        """
    )

    op.alter_column(
        "accounts",
        "is_postable",
        existing_type=sa.Boolean(),
        nullable=False,
    )

    # -------------------------------------------------
    # is_system
    # -------------------------------------------------

    op.add_column(
        "accounts",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=True,
        ),
    )

    # The full Ukrainian system chart has not yet
    # been seeded. The later chart seeding step will
    # mark matching official accounts as system ones.
    op.execute(
        """
        UPDATE accounts
        SET is_system = FALSE
        """
    )

    op.alter_column(
        "accounts",
        "is_system",
        existing_type=sa.Boolean(),
        nullable=False,
    )


def downgrade() -> None:
    """
    Return accounts to the previous schema while
    preserving all original account rows and IDs.
    """

    op.drop_column(
        "accounts",
        "is_system",
    )

    op.drop_column(
        "accounts",
        "is_postable",
    )

    op.drop_constraint(
        NORMAL_BALANCE_CONSTRAINT,
        "accounts",
        type_="check",
    )

    op.drop_column(
        "accounts",
        "normal_balance",
    )

    op.drop_constraint(
        ACCOUNT_TYPE_CONSTRAINT,
        "accounts",
        type_="check",
    )
