"""add sales commercial policy

Revision ID: a8c5e1d4f902
Revises: f7b4d9e2c835
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c5e1d4f902"
down_revision: Union[str, None] = "f7b4d9e2c835"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "counterparties",
        sa.Column(
            "default_sales_price_type_code",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        (
            "fk_counterparties_company_"
            "default_sales_price_type"
        ),
        "counterparties",
        "price_types",
        [
            "company_id",
            "default_sales_price_type_code",
        ],
        [
            "company_id",
            "code",
        ],
        ondelete="RESTRICT",
    )

    op.add_column(
        "contracts",
        sa.Column(
            "default_sales_price_type_code",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        (
            "fk_contracts_company_"
            "default_sales_price_type"
        ),
        "contracts",
        "price_types",
        [
            "company_id",
            "default_sales_price_type_code",
        ],
        [
            "company_id",
            "code",
        ],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        (
            "fk_contracts_company_"
            "default_sales_price_type"
        ),
        "contracts",
        type_="foreignkey",
    )

    op.drop_column(
        "contracts",
        "default_sales_price_type_code",
    )

    op.drop_constraint(
        (
            "fk_counterparties_company_"
            "default_sales_price_type"
        ),
        "counterparties",
        type_="foreignkey",
    )

    op.drop_column(
        "counterparties",
        "default_sales_price_type_code",
    )
