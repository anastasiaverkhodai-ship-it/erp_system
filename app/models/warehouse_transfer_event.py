from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WarehouseTransferEvent(Base):
    """
    Immutable business header for one warehouse transfer.

    history_key is the stable application-generated UUID string
    identifying one immutable correction chain:

        original
          -> reversal
          -> replacement

    Independent transfers have independent history_key values and
    may all remain ACTIVE simultaneously.
    """

    __tablename__ = "warehouse_transfer_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_wte_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "history_key",
            name="uq_wte_history_identity",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "source_warehouse_id",
            "destination_warehouse_id",
            name="uq_wte_line_parent_identity",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_wte_reversal_of",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "source_warehouse_id",
            ],
            [
                "warehouses.company_id",
                "warehouses.id",
            ],
            name="fk_wte_company_source_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "destination_warehouse_id",
            ],
            [
                "warehouses.company_id",
                "warehouses.id",
            ],
            name="fk_wte_company_destination_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "history_key",
            ],
            [
                "warehouse_transfer_events.company_id",
                "warehouse_transfer_events.id",
                "warehouse_transfer_events.history_key",
            ],
            name="fk_wte_history_reversal_of",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_warehouse_id <> destination_warehouse_id",
            name="ck_wte_distinct_warehouses",
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_wte_not_self_reversal",
        ),
        CheckConstraint(
            "length(history_key) = 36",
            name="ck_wte_history_key_length",
        ),
        Index(
            "ix_wte_company_transfer_date",
            "company_id",
            "transfer_date",
            "id",
        ),
        Index(
            "ix_wte_history_chain",
            "company_id",
            "history_key",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    history_key: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    source_warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    destination_warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    transfer_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
