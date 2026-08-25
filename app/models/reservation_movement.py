from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    func,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base
from app.services.reservation_types import (
    ReservationMovementType,
)


class ReservationMovement(Base):
    """
    Persistent reservation ledger entry.

    source_document_id and source_document_line_id currently
    reference TradeDocument / TradeDocumentLine.

    Quantity is always positive. Movement direction is defined
    by movement_type:

    RESERVE
        increases reserved quantity.

    RELEASE
        decreases reserved quantity without fulfillment.

    CONSUME
        decreases reserved quantity during fulfillment.
    """

    __tablename__ = "reservation_movements"

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "company_id",
                "source_document_id",
                "source_document_line_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
                "trade_document_lines.warehouse_id",
            ],
            name=(
                "fk_reservation_movements_"
                "trade_document_line"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity > 0",
            name=(
                "ck_reservation_movement_"
                "quantity_positive"
            ),
        ),
        CheckConstraint(
            (
                "movement_type IN ("
                "'reserve', "
                "'release', "
                "'consume'"
                ")"
            ),
            name=(
                "ck_reservation_movement_type"
            ),
        ),
        Index(
            "ix_reservation_movements_stock",
            "company_id",
            "product_id",
            "warehouse_id",
            "id",
        ),
        Index(
            "ix_reservation_movements_source_line",
            "company_id",
            "source_document_line_id",
            "id",
        ),
        Index(
            "ix_reservation_movements_source_document",
            "company_id",
            "source_document_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
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

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    source_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    source_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    movement_type: Mapped[
        ReservationMovementType
    ] = mapped_column(
        SQLEnum(
            ReservationMovementType,
            name="reservation_movement_type_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        server_default=func.now(),
        nullable=False,
    )

    @property
    def signed_quantity(
        self,
    ) -> Decimal:
        if (
            self.movement_type
            == ReservationMovementType.RESERVE
        ):
            return self.quantity

        return -self.quantity
