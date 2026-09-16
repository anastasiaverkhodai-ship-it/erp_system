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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoice(Base):
    """
    Canonical Ukrainian tax-invoice legal-document header.

    This model is deliberately separate from:
      * TaxCalculation — immutable VAT calculation snapshot;
      * TaxRecognitionEvent — first-event recognition ledger;
      * TaxCreditEvidence — INPUT VAT legal-credit evidence.

    Current V1 source identities:

      OUTPUT:
        fulfillment
        settlement
        order_advance

      INPUT:
        input_external

    Registration state is not mutated on this row. It is derived from
    immutable TaxInvoiceRegistrationEvent history.
    """

    __tablename__ = "tax_invoices"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_ti_company_id",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "source_payment_settlement_allocation_id",
            ],
            [
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ],
            name="fk_ti_settlement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "source_order_vat_advance_id",
            ],
            [
                "order_vat_advances.company_id",
                "order_vat_advances.id",
            ],
            name="fk_ti_order_advance",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('input', 'output')",
            name="ck_ti_direction",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_ti_uah",
        ),
        CheckConstraint(
            "source_kind IN "
            "('fulfillment', 'settlement', "
            "'order_advance', 'input_external')",
            name="ck_ti_source_kind",
        ),
        CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_ti_number_nonempty",
        ),
        CheckConstraint(
            "length(trim(seller_name)) > 0",
            name="ck_ti_seller_name",
        ),
        CheckConstraint(
            "length(trim(seller_tax_number)) > 0",
            name="ck_ti_seller_tax",
        ),
        CheckConstraint(
            "length(trim(seller_vat_number)) > 0",
            name="ck_ti_seller_vat",
        ),
        CheckConstraint(
            "("
            "direction = 'input' "
            "AND source_kind = 'input_external' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "direction = 'output' "
            "AND ("
            "("
            "source_kind = 'fulfillment' "
            "AND source_fulfillment_id IS NOT NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "source_kind = 'settlement' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NOT NULL "
            "AND source_order_vat_advance_id IS NULL"
            ") OR ("
            "source_kind = 'order_advance' "
            "AND source_fulfillment_id IS NULL "
            "AND source_payment_settlement_allocation_id IS NULL "
            "AND source_order_vat_advance_id IS NOT NULL"
            ")"
            ")"
            ")",
            name="ck_ti_source_state",
        ),
        Index(
            "ix_ti_company_date",
            "company_id",
            "document_date",
        ),
        Index(
            "ix_ti_number",
            "company_id",
            "document_number",
        ),
        Index(
            "ux_ti_fulfillment_source",
            "company_id",
            "source_fulfillment_id",
            unique=True,
            postgresql_where=text(
                "source_fulfillment_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ti_settlement_source",
            "company_id",
            "source_payment_settlement_allocation_id",
            unique=True,
            postgresql_where=text(
                "source_payment_settlement_allocation_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ti_advance_source",
            "company_id",
            "source_order_vat_advance_id",
            unique=True,
            postgresql_where=text(
                "source_order_vat_advance_id IS NOT NULL"
            ),
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
    )

    direction: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
    )

    document_number: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
        server_default="UAH",
    )

    seller_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    seller_tax_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    seller_vat_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    buyer_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    buyer_tax_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    buyer_vat_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    source_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    source_fulfillment_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "trade_fulfillments.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    source_payment_settlement_allocation_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    source_order_vat_advance_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
