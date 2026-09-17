from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoiceCorrectionLine(Base):
    """
    Immutable legal RK line snapshot.

    Exactly one economic provenance source is required per line.
    TaxCreditEvidence is optional secondary legal evidence and is not
    itself the economic source of the correction.
    """

    __tablename__ = "tax_invoice_correction_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticl_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "tax_invoice_correction_id",
            "line_number",
            name="uq_ticl_correction_line",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_invoice_correction_id",
            ],
            [
                "tax_invoice_corrections.company_id",
                "tax_invoice_corrections.id",
            ],
            name="fk_ticl_correction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "original_tax_invoice_line_id",
            ],
            [
                "tax_invoice_lines.company_id",
                "tax_invoice_lines.id",
            ],
            name="fk_ticl_original_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "sales_return_recognition_event_id",
            ],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
            ],
            name="fk_ticl_sales_return",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_value_correction_event_id",
            ],
            [
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ],
            name="fk_ticl_value_correction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_vat_adjustment_event_id",
            ],
            [
                "purchase_return_vat_adjustment_events.company_id",
                "purchase_return_vat_adjustment_events.id",
            ],
            name="fk_ticl_purchase_return",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_vat_adjustment_event_id",
            ],
            [
                "purchase_value_correction_vat_adjustment_events.company_id",
                "purchase_value_correction_vat_adjustment_events.id",
            ],
            name="fk_ticl_purchase_value",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_recognition_reversal_event_id",
            ],
            [
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
            ],
            name="fk_ticl_recognition_reversal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_credit_evidence_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
            ],
            name="fk_ticl_credit_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            """
            source_kind IN (
                'sales_return',
                'sales_value_correction',
                'purchase_return',
                'purchase_value_correction',
                'recognition_reversal'
            )
            """,
            name="ck_ticl_source_kind",
        ),
        CheckConstraint(
            """
            (
                source_kind = 'sales_return'
                AND sales_return_recognition_event_id IS NOT NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'sales_value_correction'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NOT NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'purchase_return'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NOT NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'purchase_value_correction'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NOT NULL
                AND tax_recognition_reversal_event_id IS NULL
            )
            OR
            (
                source_kind = 'recognition_reversal'
                AND sales_return_recognition_event_id IS NULL
                AND trade_value_correction_event_id IS NULL
                AND purchase_return_vat_adjustment_event_id IS NULL
                AND purchase_value_correction_vat_adjustment_event_id IS NULL
                AND tax_recognition_reversal_event_id IS NOT NULL
            )
            """,
            name="ck_ticl_source_shape",
        ),
        CheckConstraint(
            "line_number > 0",
            name="ck_ticl_line_positive",
        ),
        CheckConstraint(
            "length(trim(reason_code)) > 0",
            name="ck_ticl_reason",
        ),
        CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_ticl_description",
        ),
        CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_ticl_uom",
        ),
        CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_ticl_classification",
        ),
        CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_ticl_statutory_code",
        ),
        CheckConstraint(
            "length(trim(tax_rate_code)) > 0",
            name="ck_ticl_rate_code",
        ),
        CheckConstraint(
            "tax_rate >= 0",
            name="ck_ticl_rate",
        ),
        CheckConstraint(
            """
            quantity_delta <> 0
            OR unit_price_without_vat_delta <> 0
            OR taxable_base_delta <> 0
            OR tax_amount_delta <> 0
            OR total_with_vat_delta <> 0
            """,
            name="ck_ticl_nonzero_delta",
        ),
        CheckConstraint(
            """
            total_with_vat_delta
            = taxable_base_delta + tax_amount_delta
            """,
            name="ck_ticl_total_math",
        ),
        Index(
            "ix_ticl_correction",
            "company_id",
            "tax_invoice_correction_id",
            "line_number",
        ),
        Index(
            "ix_ticl_original_line",
            "company_id",
            "original_tax_invoice_line_id",
        ),
        Index(
            "ux_ticl_sales_return_source",
            "company_id",
            "sales_return_recognition_event_id",
            unique=True,
            postgresql_where=text(
                "sales_return_recognition_event_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ticl_value_source",
            "company_id",
            "trade_value_correction_event_id",
            unique=True,
            postgresql_where=text(
                "trade_value_correction_event_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ticl_purchase_return_source",
            "company_id",
            "purchase_return_vat_adjustment_event_id",
            unique=True,
            postgresql_where=text(
                "purchase_return_vat_adjustment_event_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ticl_purchase_value_source",
            "company_id",
            "purchase_value_correction_vat_adjustment_event_id",
            unique=True,
            postgresql_where=text(
                "purchase_value_correction_vat_adjustment_event_id IS NOT NULL"
            ),
        ),
        Index(
            "ux_ticl_recognition_reversal_source",
            "company_id",
            "tax_recognition_reversal_event_id",
            unique=True,
            postgresql_where=text(
                "tax_recognition_reversal_event_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_ticl_credit_evidence",
            "company_id",
            "tax_credit_evidence_id",
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

    tax_invoice_correction_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    original_tax_invoice_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    sales_return_recognition_event_id: Mapped[int | None] = (
        mapped_column(
            Integer,
            nullable=True,
        )
    )

    trade_value_correction_event_id: Mapped[int | None] = (
        mapped_column(
            Integer,
            nullable=True,
        )
    )

    purchase_return_vat_adjustment_event_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    purchase_value_correction_vat_adjustment_event_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    tax_recognition_reversal_event_id: Mapped[int | None] = (
        mapped_column(
            Integer,
            nullable=True,
        )
    )

    tax_credit_evidence_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    reason_code: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    uom_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    classification_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    statutory_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    tax_rate_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(9, 6),
        nullable=False,
    )

    quantity_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        default=Decimal("0"),
    )

    unit_price_without_vat_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
        default=Decimal("0"),
    )

    taxable_base_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
    )

    tax_amount_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
    )

    total_with_vat_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
    )
