from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class PurchaseValueCorrectionMovingAverageReplayEvent(Base):
    """
    Immutable valuation impact created by replaying one
    moving-average inventory stream after a PURCHASE
    value correction allocation.

    This is NOT a warehouse movement.

    Historical MovingAverageMovement rows remain unchanged.
    Historical InventoryCostEntry rows remain unchanged.

    effect_kind:

    issued
        A historical ISSUE would have had a different cost
        under corrected moving-average chronology.

    on_hand
        Residual inventory value differs after replay.

    Corrections are append-only:

        original -> reversal -> replacement
    """

    __tablename__ = (
        "purchase_value_correction_ma_replay_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcma_event_company_id_id",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcma_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "purchase_value_correction_allocation_event_id",
            ),
            (
                "purchase_value_correction_allocation_events.company_id",
                "purchase_value_correction_allocation_events.id",
            ),
            name="fk_pvcma_event_allocation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("product_id",),
            ("products.id",),
            name="fk_pvcma_event_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("warehouse_id",),
            ("warehouses.id",),
            name="fk_pvcma_event_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("source_moving_average_movement_id",),
            ("moving_average_movements.id",),
            name="fk_pvcma_event_source_ma_movement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("source_inventory_cost_entry_id",),
            ("inventory_cost_entries.id",),
            name="fk_pvcma_event_source_cost_entry",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("created_by",),
            ("users.id",),
            name="fk_pvcma_event_created_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
            ),
            (
                "purchase_value_correction_ma_replay_events.company_id",
                "purchase_value_correction_ma_replay_events.id",
            ),
            name="fk_pvcma_event_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "effect_kind IN ('issued', 'on_hand')",
            name="ck_pvcma_event_effect_kind",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_pvcma_event_quantity_positive",
        ),
        CheckConstraint(
            "original_valuation_amount >= 0",
            name="ck_pvcma_event_original_nonnegative",
        ),
        CheckConstraint(
            "corrected_valuation_amount >= 0",
            name="ck_pvcma_event_corrected_nonnegative",
        ),
        CheckConstraint(
            (
                "original_valuation_amount "
                "<> corrected_valuation_amount"
            ),
            name="ck_pvcma_event_not_noop",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcma_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcma_event_not_self_reversal",
        ),
        CheckConstraint(
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
        Index(
            "ix_pvcma_event_allocation",
            "company_id",
            "purchase_value_correction_allocation_event_id",
        ),
        Index(
            "ix_pvcma_event_stream",
            "company_id",
            "product_id",
            "warehouse_id",
            "recognition_date",
            "id",
        ),
        Index(
            "ix_pvcma_source_ma_movement",
            "source_moving_average_movement_id",
        ),
        Index(
            "ix_pvcma_source_cost_entry",
            "source_inventory_cost_entry_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    purchase_value_correction_allocation_event_id: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
        )
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    effect_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    source_moving_average_movement_id: Mapped[int | None] = (
        mapped_column(
            Integer,
            nullable=True,
        )
    )

    source_inventory_cost_entry_id: Mapped[int | None] = (
        mapped_column(
            Integer,
            nullable=True,
        )
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    original_valuation_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            20,
            8,
        ),
        nullable=False,
    )

    corrected_valuation_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            20,
            8,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True,
        ),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return (
            Decimal(
                self.corrected_valuation_amount
            )
            - Decimal(
                self.original_valuation_amount
            )
        )
