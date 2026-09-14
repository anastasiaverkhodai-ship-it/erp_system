from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProductPrice(Base):
    """
    Persistent effective-dated product price.

    Historical rows are effective-dated price master data.
    A later price is represented by a later effective_from row,
    not by rewriting prior business-document history.
    """

    __tablename__ = "product_prices"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_product_prices_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "product_id",
            "price_type_code",
            "uom_code",
            "effective_from",
            name="uq_product_prices_effective_identity",
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
            name="fk_product_prices_company_product",
            ondelete="RESTRICT",
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
            name="fk_product_prices_company_price_type",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_product_prices_amount_nonnegative",
        ),
        CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_product_prices_uom_nonempty",
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

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    price_type_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    uom_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
