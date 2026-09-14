from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.core.database import Base
from app.services.tax_price_types import (
    TaxPriceMode,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)

if TYPE_CHECKING:
    from app.models.trade_document import TradeDocument


class TradeDocumentLine(Base):
    __tablename__ = "trade_document_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
            name=(
                "uq_trade_document_lines_"
                "invoice_matching_source"
            ),
        ),
        UniqueConstraint(
            "trade_document_id",
            "line_number",
            name=(
                "uq_trade_document_line_"
                "document_line_number"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
            "warehouse_id",
            name=(
                "uq_trade_document_lines_"
                "reservation_source"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_document_id",
            ],
            [
                "trade_documents.company_id",
                "trade_documents.id",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_document"
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "product_id",
            ],
            [
                "products.company_id",
                "products.id",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_product"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "warehouse_id",
            ],
            [
                "warehouses.company_id",
                "warehouses.id",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_warehouse"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "line_number > 0",
            name=(
                "ck_trade_document_line_"
                "number_positive"
            ),
        ),
        CheckConstraint(
            "quantity > 0",
            name=(
                "ck_trade_document_line_"
                "quantity_positive"
            ),
        ),
        CheckConstraint(
            "unit_price >= 0",
            name=(
                "ck_trade_document_line_"
                "unit_price_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "("
                "base_unit_price IS NULL "
                "AND discount_percent IS NULL "
                "AND discount_amount_per_unit IS NULL"
                ") OR ("
                "base_unit_price IS NOT NULL "
                "AND ("
                "("
                "discount_percent IS NOT NULL "
                "AND discount_amount_per_unit IS NULL"
                ") OR ("
                "discount_percent IS NULL "
                "AND discount_amount_per_unit IS NOT NULL"
                ")"
                ")"
                ")"
            ),
            name=(
                "ck_trade_document_line_"
                "discount_state"
            ),
        ),
        CheckConstraint(
            (
                "base_unit_price IS NULL "
                "OR base_unit_price >= 0"
            ),
            name=(
                "ck_trade_document_line_"
                "base_unit_price_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "discount_percent IS NULL "
                "OR ("
                "discount_percent >= 0 "
                "AND discount_percent <= 100"
                ")"
            ),
            name=(
                "ck_trade_document_line_"
                "discount_percent_range"
            ),
        ),
        CheckConstraint(
            (
                "discount_amount_per_unit IS NULL "
                "OR discount_amount_per_unit >= 0"
            ),
            name=(
                "ck_trade_document_line_"
                "discount_amount_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "discount_amount_per_unit IS NULL "
                "OR ("
                "base_unit_price IS NOT NULL "
                "AND discount_amount_per_unit "
                "<= base_unit_price"
                ")"
            ),
            name=(
                "ck_trade_document_line_"
                "discount_amount_not_above_base"
            ),
        ),
        CheckConstraint(
            (
                "("
                "price_type_code IS NULL "
                "AND source_product_price_id IS NULL "
                "AND price_uom_code IS NULL "
                "AND price_effective_from IS NULL"
                ") OR ("
                "price_type_code IS NOT NULL "
                "AND source_product_price_id IS NOT NULL "
                "AND price_uom_code IS NOT NULL "
                "AND price_effective_from IS NOT NULL"
                ")"
            ),
            name=(
                "ck_trade_document_line_"
                "price_provenance_state"
            ),
        ),
        CheckConstraint(
            (
                "price_type_code IS NULL "
                "OR length(trim(price_type_code)) > 0"
            ),
            name=(
                "ck_trade_document_line_"
                "price_type_code_nonempty"
            ),
        ),
        CheckConstraint(
            (
                "price_uom_code IS NULL "
                "OR length(trim(price_uom_code)) > 0"
            ),
            name=(
                "ck_trade_document_line_"
                "price_uom_code_nonempty"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "price_type_code",
            ],
            [
                "price_types.company_id",
                "price_types.code",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_price_type"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "source_product_price_id",
            ],
            [
                "product_prices.company_id",
                "product_prices.id",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_source_product_price"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "(tax_rate_code IS NULL "
                "AND tax_recognition_method IS NULL) "
                "OR "
                "(tax_rate_code IS NOT NULL "
                "AND tax_recognition_method IS NOT NULL)"
            ),
            name=(
                "ck_trade_document_line_"
                "tax_config_pair"
            ),
        ),
        CheckConstraint(
            (
                "tax_rate_code IS NULL "
                "OR length(trim(tax_rate_code)) > 0"
            ),
            name=(
                "ck_trade_document_line_"
                "tax_rate_code_nonempty"
            ),
        ),
        CheckConstraint(
            (
                "tax_recognition_method IS NULL "
                "OR tax_recognition_method IN "
                "('first_event', 'cash_method', 'manual')"
            ),
            name=(
                "ck_trade_document_line_"
                "tax_recognition_method"
            ),
        ),
        CheckConstraint(
            (
                "(tax_rate_code IS NULL "
                "AND tax_price_mode IS NULL) "
                "OR "
                "(tax_rate_code IS NOT NULL "
                "AND tax_price_mode IS NOT NULL)"
            ),
            name=(
                "ck_trade_document_line_"
                "tax_price_config_pair"
            ),
        ),
        CheckConstraint(
            (
                "tax_price_mode IS NULL "
                "OR tax_price_mode IN "
                "('exclusive', 'inclusive')"
            ),
            name=(
                "ck_trade_document_line_"
                "tax_price_mode"
            ),
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

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        default=Decimal("0"),
        nullable=False,
    )
    base_unit_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4),
        nullable=True,
    )
    discount_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4),
        nullable=True,
    )
    discount_amount_per_unit: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4),
        nullable=True,
    )
    price_type_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    source_product_price_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    price_uom_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    price_effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    tax_rate_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    tax_recognition_method: Mapped[
        TaxRecognitionMethod | None
    ] = mapped_column(
        SQLEnum(
            TaxRecognitionMethod,
            name=(
                "trade_document_line_"
                "tax_recognition_method_enum"
            ),
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=20,
        ),
        nullable=True,
    )

    tax_price_mode: Mapped[
        TaxPriceMode | None
    ] = mapped_column(
        SQLEnum(
            TaxPriceMode,
            name=(
                "trade_document_line_"
                "tax_price_mode_enum"
            ),
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=20,
        ),
        nullable=True,
    )

    trade_document: Mapped["TradeDocument"] = relationship(
        "TradeDocument",
        back_populates="lines",
    )
