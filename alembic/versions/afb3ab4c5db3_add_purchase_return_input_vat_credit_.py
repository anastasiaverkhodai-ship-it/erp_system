"""add purchase return input vat credit correction event

Revision ID: afb3ab4c5db3
Revises: 3e637bacc962
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "afb3ab4c5db3"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "3e637bacc962"
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


def upgrade() -> None:
    op.create_table(
        "purchase_return_input_vat_credit_correction_events",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "purchase_return_vat_adjustment_event_id",
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
            "reduced_taxable_base",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "reduced_tax_amount",
            sa.Numeric(
                precision=18,
                scale=2,
            ),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(
                length=3
            ),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(
                timezone=True
            ),
            server_default=sa.text(
                "now()"
            ),
            nullable=False,
        ),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.CheckConstraint(
            "reduced_taxable_base >= 0",
            name=(
                "ck_privcc_event_"
                "taxable_base_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "reduced_tax_amount >= 0",
            name=(
                "ck_privcc_event_"
                "tax_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "reduced_taxable_base > 0 "
            "OR reduced_tax_amount > 0",
            name=(
                "ck_privcc_event_"
                "nonzero_correction"
            ),
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_privcc_event_"
                "currency_length"
            ),
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL "
            "OR reversal_of_id <> id",
            name=(
                "ck_privcc_event_"
                "not_self_reversal"
            ),
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_vat_adjustment_event_id",
            ],
            [
                "purchase_return_vat_adjustment_events.company_id",
                "purchase_return_vat_adjustment_events.id",
            ],
            name=(
                "fk_privcc_event_company_"
                "purchase_return_vat_adjustment"
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
            name=(
                "fk_privcc_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "purchase_return_vat_adjustment_event_id",
                "tax_calculation_id",
            ],
            [
                "purchase_return_input_vat_credit_correction_events.company_id",
                "purchase_return_input_vat_credit_correction_events.id",
                "purchase_return_input_vat_credit_correction_events.purchase_return_vat_adjustment_event_id",
                "purchase_return_input_vat_credit_correction_events.tax_calculation_id",
            ],
            name=(
                "fk_privcc_event_"
                "reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
            ],
            [
                "companies.id",
            ],
            name=(
                "fk_privcc_event_company"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "created_by",
            ],
            [
                "users.id",
            ],
            name=(
                "fk_privcc_event_created_by"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=(
                "pk_purchase_return_input_vat_"
                "credit_correction_events"
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_privcc_event_"
                "company_id_id"
            ),
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "purchase_return_vat_adjustment_event_id",
            "tax_calculation_id",
            name=(
                "uq_privcc_event_"
                "company_id_id_source"
            ),
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_privcc_event_"
                "reversal_of"
            ),
        ),
    )

    op.create_index(
        "ix_privcc_event_source_history",
        "purchase_return_input_vat_credit_correction_events",
        [
            "company_id",
            "purchase_return_vat_adjustment_event_id",
            "tax_calculation_id",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_privcc_event_tax_calculation",
        "purchase_return_input_vat_credit_correction_events",
        [
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_privcc_event_adjustment_date",
        "purchase_return_input_vat_credit_correction_events",
        [
            "company_id",
            "adjustment_date",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_privcc_event_adjustment_date",
        table_name=(
            "purchase_return_input_vat_credit_correction_events"
        ),
    )

    op.drop_index(
        "ix_privcc_event_tax_calculation",
        table_name=(
            "purchase_return_input_vat_credit_correction_events"
        ),
    )

    op.drop_index(
        "ix_privcc_event_source_history",
        table_name=(
            "purchase_return_input_vat_credit_correction_events"
        ),
    )

    op.drop_table(
        "purchase_return_input_vat_credit_correction_events"
    )
