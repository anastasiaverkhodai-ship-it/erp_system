"""add sales recognition journal entry source

Revision ID: d54e8c2a71f3
Revises: b664d727d446

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d54e8c2a71f3"
down_revision: Union[str, Sequence[str], None] = "b664d727d446"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_CHECK_FIVE = """
(
    document_id IS NULL
    OR payment_id IS NULL
)
AND
(
    document_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    document_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    document_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    payment_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR sales_recognition_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR sales_recognition_event_id IS NULL
)
"""


SOURCE_CHECK_FOUR = """
(
    document_id IS NULL
    OR payment_id IS NULL
)
AND
(
    document_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    document_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR payment_settlement_allocation_id IS NULL
)
AND
(
    payment_id IS NULL
    OR tax_recognition_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR tax_recognition_event_id IS NULL
)
"""


def upgrade() -> None:

    op.add_column(
        "journal_entries",
        sa.Column(
            "sales_recognition_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_journal_entries_sales_recognition_event_id",
        "journal_entries",
        ["sales_recognition_event_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_journal_entries_company_sales_recognition_event",
        "journal_entries",
        "sales_recognition_events",
        [
            "company_id",
            "sales_recognition_event_id",
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        SOURCE_CHECK_FIVE,
    )

    op.create_index(
        "uq_journal_entry_original_sales_recognition_event",
        "journal_entries",
        ["sales_recognition_event_id"],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND sales_recognition_event_id IS NOT NULL"
        ),
    )


def downgrade() -> None:

    op.drop_index(
        "uq_journal_entry_original_sales_recognition_event",
        table_name="journal_entries",
    )

    op.drop_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        "ck_journal_entries_at_most_one_business_source",
        "journal_entries",
        SOURCE_CHECK_FOUR,
    )

    op.drop_constraint(
        "fk_journal_entries_company_sales_recognition_event",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_journal_entries_sales_recognition_event_id",
        table_name="journal_entries",
    )

    op.drop_column(
        "journal_entries",
        "sales_recognition_event_id",
    )
