"""add tax recognition journal entry source

Revision ID: 769e783aca5b
Revises: a09dad8c4dea
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '769e783aca5b'
down_revision: Union[str, Sequence[str], None] = 'a09dad8c4dea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "tax_recognition_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_journal_entries_tax_recognition_event_id",
        "journal_entries",
        ["tax_recognition_event_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_journal_entries_company_tax_recognition_event",
        "journal_entries",
        "tax_recognition_events",
        [
            "company_id",
            "tax_recognition_event_id",
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
        '\n            (\n                document_id IS NULL\n                OR payment_id IS NULL\n            )\n            AND\n            (\n                document_id IS NULL\n                OR payment_settlement_allocation_id IS NULL\n            )\n            AND\n            (\n                document_id IS NULL\n                OR tax_recognition_event_id IS NULL\n            )\n            AND\n            (\n                payment_id IS NULL\n                OR payment_settlement_allocation_id IS NULL\n            )\n            AND\n            (\n                payment_id IS NULL\n                OR tax_recognition_event_id IS NULL\n            )\n            AND\n            (\n                payment_settlement_allocation_id IS NULL\n                OR tax_recognition_event_id IS NULL\n            )\n',
    )

    op.create_index(
        "uq_journal_entry_original_tax_recognition_event",
        "journal_entries",
        ["tax_recognition_event_id"],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND tax_recognition_event_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_journal_entry_original_tax_recognition_event",
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
        '\n            (\n                document_id IS NULL\n                OR payment_id IS NULL\n            )\n            AND\n            (\n                document_id IS NULL\n                OR payment_settlement_allocation_id IS NULL\n            )\n            AND\n            (\n                payment_id IS NULL\n                OR payment_settlement_allocation_id IS NULL\n            )\n',
    )

    op.drop_constraint(
        "fk_journal_entries_company_tax_recognition_event",
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_journal_entries_tax_recognition_event_id",
        table_name="journal_entries",
    )

    op.drop_column(
        "journal_entries",
        "tax_recognition_event_id",
    )
