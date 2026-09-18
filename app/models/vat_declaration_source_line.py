from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VatDeclarationSourceLine(Base):
    __tablename__ = "vat_declaration_source_lines"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False)
    vat_declaration_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    tax_recognition_event_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    sales_return_recognition_event_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trade_value_correction_event_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    purchase_return_input_vat_credit_correction_event_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )
    purchase_value_correction_input_vat_credit_correction_event_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    economic_effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    taxable_base_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    tax_amount_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
    )

    source_reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    tax_rate_code: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    tax_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_vdsl_company", "company_id"),
        ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdsl_decl_tenant",
        ),
        ForeignKeyConstraint(
            ["company_id", "tax_recognition_event_id"],
            ["tax_recognition_events.company_id", "tax_recognition_events.id"],
            name="fk_vdsl_taxrec_tenant",
        ),
        ForeignKeyConstraint(
            ["company_id", "sales_return_recognition_event_id"],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
            ],
            name="fk_vdsl_sret_tenant",
        ),
        ForeignKeyConstraint(
            ["company_id", "trade_value_correction_event_id"],
            [
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ],
            name="fk_vdsl_tvc_tenant",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_input_vat_credit_correction_event_id",
            ],
            [
                "purchase_return_input_vat_credit_correction_events.company_id",
                "purchase_return_input_vat_credit_correction_events.id",
            ],
            name="fk_vdsl_pricc_tenant",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_value_correction_input_vat_credit_correction_event_id",
            ],
            [
                "purchase_value_correction_input_vat_credit_correction_events.company_id",
                "purchase_value_correction_input_vat_credit_correction_events.id",
            ],
            name="fk_vdsl_pvicc_tenant",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdsl_company_id",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "line_number",
            name="uq_vdsl_decl_line",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "tax_recognition_event_id",
            name="uq_vdsl_decl_taxrec",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "sales_return_recognition_event_id",
            name="uq_vdsl_decl_sret",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "trade_value_correction_event_id",
            name="uq_vdsl_decl_tvc",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "purchase_return_input_vat_credit_correction_event_id",
            name="uq_vdsl_decl_pricc",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "purchase_value_correction_input_vat_credit_correction_event_id",
            name="uq_vdsl_decl_pvicc",
        ),
        CheckConstraint(
            "line_number >= 1",
            name="ck_vdsl_line_no",
        ),
        CheckConstraint(
            "source_kind IN ("
            "'output_tax_recognition', "
            "'input_tax_recognition', "
            "'sales_return', "
            "'sales_value_correction', "
            "'purchase_return_input_credit_correction', "
            "'purchase_value_input_credit_correction'"
            ")",
            name="ck_vdsl_source_kind",
        ),
        CheckConstraint(
            "direction IN ('output', 'input')",
            name="ck_vdsl_direction",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_vdsl_uah",
        ),
        CheckConstraint(
            "taxable_base_delta <> 0 OR tax_amount_delta <> 0",
            name="ck_vdsl_nonzero",
        ),
        CheckConstraint(
            "num_nonnulls("
            "tax_recognition_event_id, "
            "sales_return_recognition_event_id, "
            "trade_value_correction_event_id, "
            "purchase_return_input_vat_credit_correction_event_id, "
            "purchase_value_correction_input_vat_credit_correction_event_id"
            ") = 1",
            name="ck_vdsl_one_source",
        ),
        CheckConstraint(
            "("
            "source_kind IN ('output_tax_recognition', 'input_tax_recognition') "
            "AND tax_recognition_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'sales_return' "
            "AND sales_return_recognition_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'sales_value_correction' "
            "AND trade_value_correction_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'purchase_return_input_credit_correction' "
            "AND purchase_return_input_vat_credit_correction_event_id IS NOT NULL"
            ") OR ("
            "source_kind = 'purchase_value_input_credit_correction' "
            "AND purchase_value_correction_input_vat_credit_correction_event_id "
            "IS NOT NULL"
            ")",
            name="ck_vdsl_kind_fk",
        ),
        CheckConstraint(
            "("
            "source_kind IN ("
            "'output_tax_recognition', "
            "'sales_return', "
            "'sales_value_correction'"
            ") AND direction = 'output'"
            ") OR ("
            "source_kind IN ("
            "'input_tax_recognition', "
            "'purchase_return_input_credit_correction', "
            "'purchase_value_input_credit_correction'"
            ") AND direction = 'input'"
            ")",
            name="ck_vdsl_kind_dir",
        ),
    )
