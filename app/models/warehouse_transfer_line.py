from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.document import DocumentType


class WarehouseTransferLine(Base):
    """
    Immutable BUSINESS transfer line.

    Cardinality:
        one business transfer line
          -> exactly one source ISSUE DocumentLine
          -> one destination RECEIPT Document
          -> one or many valuation layers

    FIFO:
        source ISSUE line may consume N StockLotConsumption rows,
        therefore this row MUST NOT identify one receipt DocumentLine.

    Moving average:
        normally one valuation layer / one receipt DocumentLine.
    """

    __tablename__ = "warehouse_transfer_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            "product_id",
            "destination_warehouse_id",
            "receipt_document_id",
            name="uq_wtl_valuation_parent_identity",
        ),
        UniqueConstraint(
            "issue_document_line_id",
            name="uq_wtl_issue_document_line",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "transfer_event_id",
                "source_warehouse_id",
                "destination_warehouse_id",
            ],
            [
                "warehouse_transfer_events.company_id",
                "warehouse_transfer_events.id",
                "warehouse_transfer_events.source_warehouse_id",
                "warehouse_transfer_events.destination_warehouse_id",
            ],
            name="fk_wtl_transfer_parent_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "issue_document_id",
                "issue_document_type",
            ],
            [
                "documents.company_id",
                "documents.id",
                "documents.document_type",
            ],
            name="fk_wtl_company_issue_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "receipt_document_id",
                "receipt_document_type",
            ],
            [
                "documents.company_id",
                "documents.id",
                "documents.document_type",
            ],
            name="fk_wtl_company_receipt_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "issue_document_id",
                "issue_document_line_id",
                "product_id",
                "source_warehouse_id",
            ],
            [
                "document_lines.document_id",
                "document_lines.id",
                "document_lines.product_id",
                "document_lines.warehouse_id",
            ],
            name="fk_wtl_issue_document_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_wtl_quantity_positive",
        ),
        CheckConstraint(
            "source_warehouse_id <> destination_warehouse_id",
            name="ck_wtl_distinct_warehouses",
        ),
        CheckConstraint(
            "issue_document_type = 'issue'",
            name="ck_wtl_issue_document_type",
        ),
        CheckConstraint(
            "receipt_document_type = 'receipt'",
            name="ck_wtl_receipt_document_type",
        ),
        CheckConstraint(
            "issue_document_id <> receipt_document_id",
            name="ck_wtl_distinct_documents",
        ),
        Index(
            "ix_wtl_transfer_event",
            "company_id",
            "transfer_event_id",
            "id",
        ),
        Index(
            "ix_wtl_product",
            "company_id",
            "product_id",
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
        index=True,
    )

    transfer_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
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

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    issue_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    issue_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    issue_document_type: Mapped[DocumentType] = mapped_column(
        SQLEnum(
            DocumentType,
            name="document_type_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        default=DocumentType.ISSUE,
    )

    receipt_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    receipt_document_type: Mapped[DocumentType] = mapped_column(
        SQLEnum(
            DocumentType,
            name="document_type_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        default=DocumentType.RECEIPT,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
