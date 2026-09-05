"""add purchase return vat adjustment journal source

Revision ID: 3e637bacc962
Revises: 9c7ffc7a2182
"""

from itertools import combinations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e637bacc962"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "9c7ffc7a2182"
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


BUSINESS_SOURCES_BEFORE = (
    "document_id",
    "payment_id",
    "payment_settlement_allocation_id",
    "tax_recognition_event_id",
    "sales_recognition_event_id",
    "vat_advance_bridge_event_id",
    "input_vat_fulfillment_bridge_event_id",
    "supplier_advance_clearing_event_id",
    "customer_advance_clearing_event_id",
    "sales_return_recognition_event_id",
    "sales_return_cost_restoration_event_id",
    "purchase_return_recognition_event_id",
)

BUSINESS_SOURCES_AFTER = (
    *BUSINESS_SOURCES_BEFORE,
    "purchase_return_vat_adjustment_event_id",
)


def source_check(
    columns: tuple[
        str,
        ...,
    ],
) -> sa.TextClause:
    return sa.text(
        "\nAND\n".join(
            (
                f"({left} IS NULL "
                f"OR {right} IS NULL)"
            )
            for left, right
            in combinations(
                columns,
                2,
            )
        )
    )


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "purchase_return_vat_adjustment_event_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_index(
        (
            "ix_journal_entries_"
            "purchase_return_vat_adjustment_event_id"
        ),
        "journal_entries",
        [
            "purchase_return_vat_adjustment_event_id",
        ],
        unique=False,
    )

    op.create_foreign_key(
        (
            "fk_journal_entries_company_"
            "purchase_return_vat_adjustment_event"
        ),
        "journal_entries",
        "purchase_return_vat_adjustment_events",
        [
            "company_id",
            "purchase_return_vat_adjustment_event_id",
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
        source_check(
            BUSINESS_SOURCES_AFTER
        ),
    )

    op.create_index(
        (
            "uq_journal_entry_original_"
            "purchase_return_vat_adjustment_event"
        ),
        "journal_entries",
        [
            "purchase_return_vat_adjustment_event_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "reversal_of_id IS NULL "
            "AND purchase_return_vat_adjustment_event_id "
            "IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        (
            "uq_journal_entry_original_"
            "purchase_return_vat_adjustment_event"
        ),
        table_name="journal_entries",
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
        source_check(
            BUSINESS_SOURCES_BEFORE
        ),
    )

    op.drop_constraint(
        (
            "fk_journal_entries_company_"
            "purchase_return_vat_adjustment_event"
        ),
        "journal_entries",
        type_="foreignkey",
    )

    op.drop_index(
        (
            "ix_journal_entries_"
            "purchase_return_vat_adjustment_event_id"
        ),
        table_name="journal_entries",
    )

    op.drop_column(
        "journal_entries",
        "purchase_return_vat_adjustment_event_id",
    )
