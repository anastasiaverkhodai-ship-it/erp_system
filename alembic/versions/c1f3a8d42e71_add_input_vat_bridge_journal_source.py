"""add INPUT VAT fulfillment bridge journal source

Revision ID: c1f3a8d42e71
Revises: badc72a56036
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1f3a8d42e71"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "badc72a56036"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


OLD_SOURCE_CHECK = """
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
AND
(
    document_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR vat_advance_bridge_event_id IS NULL
)
"""


NEW_SOURCE_CHECK = (
    OLD_SOURCE_CHECK
    + """
AND
(
    document_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    payment_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    payment_settlement_allocation_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    tax_recognition_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    sales_recognition_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
AND
(
    vat_advance_bridge_event_id IS NULL
    OR input_vat_fulfillment_bridge_event_id IS NULL
)
"""
)


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "input_vat_fulfillment_bridge_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        (
            "ix_journal_entries_"
            "input_vat_fulfillment_bridge_event_id"
        ),
        "journal_entries",
        [
            "input_vat_fulfillment_bridge_event_id",
        ],
        unique=False,
    )

    op.create_index(
        (
            "uq_journal_entry_original_"
            "input_vat_fulfillment_bridge_event"
        ),
        "journal_entries",
        [
            "input_vat_fulfillment_bridge_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND input_vat_fulfillment_bridge_event_id "
            "IS NOT NULL"
        ),
    )

    op.create_foreign_key(
        (
            "fk_journal_entries_company_"
            "input_vat_fulfillment_bridge_event"
        ),
        "journal_entries",
        "input_vat_fulfillment_bridge_events",
        [
            "company_id",
            "input_vat_fulfillment_bridge_event_id",
        ],
        [
            "company_id",
            "id",
        ],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        (
            "ck_journal_entries_"
            "at_most_one_business_source"
        ),
        "journal_entries",
        type_="check",
    )

    op.create_check_constraint(
        (
            "ck_journal_entries_"
            "at_most_one_business_source"
        ),
        "journal_entries",
        sa.text(
            NEW_SOURCE_CHECK
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        (
            "ck_journal_entries_"
            "at_most_one_business_source"
        ),
        "journal_entries",
        type_="check",
    )

    op.drop_constraint(
        (
            "fk_journal_entries_company_"
            "input_vat_fulfillment_bridge_event"
        ),
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_index(
        (
            "uq_journal_entry_original_"
            "input_vat_fulfillment_bridge_event"
        ),
        table_name="journal_entries",
    )

    op.drop_index(
        (
            "ix_journal_entries_"
            "input_vat_fulfillment_bridge_event_id"
        ),
        table_name="journal_entries",
    )

    op.drop_column(
        "journal_entries",
        "input_vat_fulfillment_bridge_event_id",
    )

    op.create_check_constraint(
        (
            "ck_journal_entries_"
            "at_most_one_business_source"
        ),
        "journal_entries",
        sa.text(
            OLD_SOURCE_CHECK
        ),
    )
