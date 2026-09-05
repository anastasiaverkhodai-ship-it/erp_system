"""add purchase return vat adjustment events

Revision ID: 9c7ffc7a2182
Revises: b74d9e2c6a31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c7ffc7a2182"
down_revision: Union[str, Sequence[str], None] = "b74d9e2c6a31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "purchase_return_vat_adjustment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "purchase_return_recognition_event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "tax_calculation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "adjustment_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "basis_kind",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "adjusted_taxable_base",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column(
            "adjusted_tax_amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.CheckConstraint(
            (
                "basis_kind IN ("
                "'goods_received_by_supplier', "
                "'refund_by_supplier'"
                ")"
            ),
            name="ck_prvae_event_basis_kind",
        ),
        sa.CheckConstraint(
            "adjusted_taxable_base >= 0",
            name="ck_prvae_event_taxable_base_nonnegative",
        ),
        sa.CheckConstraint(
            "adjusted_tax_amount >= 0",
            name="ck_prvae_event_tax_nonnegative",
        ),
        sa.CheckConstraint(
            (
                "adjusted_taxable_base > 0 "
                "OR adjusted_tax_amount > 0"
            ),
            name="ck_prvae_event_nonzero_adjustment",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_prvae_event_currency_length",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_prvae_event_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_recognition_event_id",
            ],
            [
                "purchase_return_recognition_events.company_id",
                "purchase_return_recognition_events.id",
            ],
            name=(
                "fk_prvae_event_company_"
                "purchase_return_recognition"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "tax_calculation_id",
            ],
            [
                "tax_calculations.company_id",
                "tax_calculations.id",
            ],
            name="fk_prvae_event_company_tax_calculation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "purchase_return_recognition_event_id",
                "tax_calculation_id",
                "basis_kind",
            ],
            [
                (
                    "purchase_return_vat_adjustment_events."
                    "company_id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "purchase_return_recognition_event_id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "tax_calculation_id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "basis_kind"
                ),
            ],
            name="fk_prvae_event_reversal_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_prvae_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "purchase_return_recognition_event_id",
            "tax_calculation_id",
            "basis_kind",
            name="uq_prvae_event_company_id_id_source",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_prvae_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_prvae_event_source_history",
        "purchase_return_vat_adjustment_events",
        [
            "company_id",
            "purchase_return_recognition_event_id",
            "tax_calculation_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_prvae_event_tax_calculation",
        "purchase_return_vat_adjustment_events",
        [
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_prvae_event_adjustment_date",
        "purchase_return_vat_adjustment_events",
        [
            "company_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prvae_event_adjustment_date",
        table_name="purchase_return_vat_adjustment_events",
    )

    op.drop_index(
        "ix_prvae_event_tax_calculation",
        table_name="purchase_return_vat_adjustment_events",
    )

    op.drop_index(
        "ix_prvae_event_source_history",
        table_name="purchase_return_vat_adjustment_events",
    )

    op.drop_table(
        "purchase_return_vat_adjustment_events"
    )
