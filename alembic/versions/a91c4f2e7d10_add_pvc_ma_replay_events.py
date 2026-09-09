"""add pvc moving average replay events

Revision ID: a91c4f2e7d10
Revises: f7a4c2d19b63
"""

from alembic import op
import sqlalchemy as sa


revision = "a91c4f2e7d10"
down_revision = "f7a4c2d19b63"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_value_correction_ma_replay_events",
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
            "purchase_value_correction_allocation_event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "effect_kind",
            sa.String(
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "source_moving_average_movement_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "source_inventory_cost_entry_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "recognition_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "quantity",
            sa.Numeric(
                precision=18,
                scale=4,
            ),
            nullable=False,
        ),
        sa.Column(
            "original_valuation_amount",
            sa.Numeric(
                precision=20,
                scale=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "corrected_valuation_amount",
            sa.Numeric(
                precision=20,
                scale=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "currency_code",
            sa.String(
                length=3,
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
                timezone=True,
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
            "effect_kind IN ('issued', 'on_hand')",
            name="ck_pvcma_event_effect_kind",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_pvcma_event_quantity_positive",
        ),
        sa.CheckConstraint(
            "original_valuation_amount >= 0",
            name="ck_pvcma_event_original_nonnegative",
        ),
        sa.CheckConstraint(
            "corrected_valuation_amount >= 0",
            name="ck_pvcma_event_corrected_nonnegative",
        ),
        sa.CheckConstraint(
            (
                "original_valuation_amount "
                "<> corrected_valuation_amount"
            ),
            name="ck_pvcma_event_not_noop",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcma_event_currency_length",
        ),
        sa.CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcma_event_not_self_reversal",
        ),
        sa.CheckConstraint(
            """
            (
                effect_kind = 'issued'
                AND source_moving_average_movement_id IS NOT NULL
                AND source_inventory_cost_entry_id IS NOT NULL
            )
            OR
            (
                effect_kind = 'on_hand'
                AND source_moving_average_movement_id IS NULL
                AND source_inventory_cost_entry_id IS NULL
            )
            """,
            name="ck_pvcma_event_source_shape",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_allocation_event_id",
            ],
            [
                "purchase_value_correction_allocation_events.company_id",
                "purchase_value_correction_allocation_events.id",
            ],
            name="fk_pvcma_event_allocation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "product_id",
            ],
            [
                "products.id",
            ],
            name="fk_pvcma_event_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "warehouse_id",
            ],
            [
                "warehouses.id",
            ],
            name="fk_pvcma_event_warehouse",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_moving_average_movement_id",
            ],
            [
                "moving_average_movements.id",
            ],
            name="fk_pvcma_event_source_ma_movement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_inventory_cost_entry_id",
            ],
            [
                "inventory_cost_entries.id",
            ],
            name="fk_pvcma_event_source_cost_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "created_by",
            ],
            [
                "users.id",
            ],
            name="fk_pvcma_event_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
            ],
            [
                "purchase_value_correction_ma_replay_events.company_id",
                "purchase_value_correction_ma_replay_events.id",
            ],
            name="fk_pvcma_event_reversal",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcma_event_company_id_id",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcma_event_reversal_of",
        ),
    )

    op.create_index(
        "ix_pvcma_event_allocation",
        "purchase_value_correction_ma_replay_events",
        [
            "company_id",
            "purchase_value_correction_allocation_event_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcma_event_stream",
        "purchase_value_correction_ma_replay_events",
        [
            "company_id",
            "product_id",
            "warehouse_id",
            "recognition_date",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcma_source_ma_movement",
        "purchase_value_correction_ma_replay_events",
        [
            "source_moving_average_movement_id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_pvcma_source_cost_entry",
        "purchase_value_correction_ma_replay_events",
        [
            "source_inventory_cost_entry_id",
        ],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_pvcma_source_cost_entry",
        table_name="purchase_value_correction_ma_replay_events",
    )

    op.drop_index(
        "ix_pvcma_source_ma_movement",
        table_name="purchase_value_correction_ma_replay_events",
    )

    op.drop_index(
        "ix_pvcma_event_stream",
        table_name="purchase_value_correction_ma_replay_events",
    )

    op.drop_index(
        "ix_pvcma_event_allocation",
        table_name="purchase_value_correction_ma_replay_events",
    )

    op.drop_table(
        "purchase_value_correction_ma_replay_events"
    )
