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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.company import InventoryValuationMethod


class WarehouseTransferValuationLayer(Base):
    """
    Immutable valuation provenance for one slice of a business
    warehouse transfer line.

    FIFO:
      one row per source StockLotConsumption;
      destination receipt DocumentLine gets exactly that layer's
      quantity and unit_cost.

    Moving average:
      normally one row for the entire business transfer line;
      source_stock_lot_consumption_id is NULL.

    source_inventory_cost_entry_id is always retained because the
    source ISSUE creates one immutable InventoryCostEntry regardless
    of valuation method.
    """

    __tablename__ = "warehouse_transfer_valuation_layers"

    __table_args__ = (
        UniqueConstraint(
            "destination_receipt_document_line_id",
            name="uq_wtvl_destination_receipt_line",
        ),
        UniqueConstraint(
            "source_stock_lot_consumption_id",
            name="uq_wtvl_source_fifo_consumption",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "transfer_line_id",
                "product_id",
                "destination_warehouse_id",
                "destination_receipt_document_id",
            ],
            [
                "warehouse_transfer_lines.company_id",
                "warehouse_transfer_lines.id",
                "warehouse_transfer_lines.product_id",
                "warehouse_transfer_lines.destination_warehouse_id",
                "warehouse_transfer_lines.receipt_document_id",
            ],
            name="fk_wtvl_transfer_line_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "destination_receipt_document_id",
                "destination_receipt_document_line_id",
                "product_id",
                "destination_warehouse_id",
            ],
            [
                "document_lines.document_id",
                "document_lines.id",
                "document_lines.product_id",
                "document_lines.warehouse_id",
            ],
            name="fk_wtvl_destination_receipt_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_wtvl_quantity_positive",
        ),
        CheckConstraint(
            "unit_cost >= 0",
            name="ck_wtvl_unit_cost_nonnegative",
        ),
        CheckConstraint(
            "valuation_amount >= 0",
            name="ck_wtvl_valuation_amount_nonnegative",
        ),
        Index(
            "ix_wtvl_transfer_line",
            "company_id",
            "transfer_line_id",
            "id",
        ),
        Index(
            "ix_wtvl_company_id",
            "company_id",
        ),
        Index(
            "ix_wtvl_transfer_line_id",
            "transfer_line_id",
        ),
        Index(
            "ix_wtvl_product_id",
            "product_id",
        ),
        Index(
            "ix_wtvl_destination_warehouse_id",
            "destination_warehouse_id",
        ),
        Index(
            "ix_wtvl_source_inventory_cost_entry_id",
            "source_inventory_cost_entry_id",
        ),
        Index(
            "ix_wtvl_source_stock_lot_consumption_id",
            "source_stock_lot_consumption_id",
        ),
        Index(
            "ix_wtvl_destination_receipt_document_id",
            "destination_receipt_document_id",
        ),
        Index(
            "ix_wtvl_destination_receipt_document_line_id",
            "destination_receipt_document_line_id",
        ),
        Index(
            "ix_wtvl_source_inventory_cost_entry",
            "source_inventory_cost_entry_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    transfer_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    destination_warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    valuation_method: Mapped[
        InventoryValuationMethod
    ] = mapped_column(
        SQLEnum(
            InventoryValuationMethod,
            name="inventory_valuation_method_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    valuation_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    source_inventory_cost_entry_id: Mapped[int] = mapped_column(
        ForeignKey(
            "inventory_cost_entries.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=False,
    )

    source_stock_lot_consumption_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "stock_lot_consumptions.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=False,
    )

    destination_receipt_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    destination_receipt_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
