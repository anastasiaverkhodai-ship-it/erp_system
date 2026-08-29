from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_treatment_types import (
    TaxTreatment,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


class TaxCalculation(Base):
    """
    Persistent immutable tax-calculation snapshot for one
    TradeDocumentLine.

    The taxable source is a commercial Trade Invoice line.

    This model stores the calculated tax basis and tax amount only.
    Recognition state is deliberately NOT persisted here.
    Recognized and remaining amounts are derived later from persistent
    TaxRecognitionEvent rows.

    One source line may have at most one calculation per TaxType.
    """

    __tablename__ = "tax_calculations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_tax_calculations_company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            "trade_document_line_id",
            "tax_type",
            name=(
                "uq_tax_calculations_"
                "source_line_tax_type"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_document_id",
                "trade_document_line_id",
                "product_id",
            ],
            [
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
            ],
            name=(
                "fk_tax_calculations_"
                "company_trade_document_line_product"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "tax_type IN ('vat')",
            name=(
                "ck_tax_calculations_tax_type"
            ),
        ),
        CheckConstraint(
            "direction IN ('input', 'output')",
            name=(
                "ck_tax_calculations_direction"
            ),
        ),
        CheckConstraint(
            (
                "treatment IN ("
                "'taxable', "
                "'zero_rated', "
                "'exempt', "
                "'out_of_scope'"
                ")"
            ),
            name=(
                "ck_tax_calculations_treatment"
            ),
        ),
        CheckConstraint(
            (
                "recognition_method IN ("
                "'first_event', "
                "'cash_method', "
                "'manual'"
                ")"
            ),
            name=(
                "ck_tax_calculations_"
                "recognition_method"
            ),
        ),
        CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 1",
            name=(
                "ck_tax_calculations_"
                "tax_rate_range"
            ),
        ),
        CheckConstraint(
            "taxable_base >= 0",
            name=(
                "ck_tax_calculations_"
                "taxable_base_nonnegative"
            ),
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name=(
                "ck_tax_calculations_"
                "tax_amount_nonnegative"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_tax_calculations_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            (
                "char_length(trim(tax_rate_code)) "
                "> 0"
            ),
            name=(
                "ck_tax_calculations_"
                "tax_rate_code_nonempty"
            ),
        ),
        CheckConstraint(
            (
                "("
                "treatment = 'taxable' "
                "AND tax_rate > 0"
                ") OR ("
                "treatment IN ("
                "'zero_rated', "
                "'exempt', "
                "'out_of_scope'"
                ") "
                "AND tax_rate = 0"
                ")"
            ),
            name=(
                "ck_tax_calculations_"
                "treatment_rate_consistency"
            ),
        ),
        CheckConstraint(
            (
                "treatment NOT IN ("
                "'zero_rated', "
                "'exempt', "
                "'out_of_scope'"
                ") "
                "OR tax_amount = 0"
            ),
            name=(
                "ck_tax_calculations_"
                "zero_tax_treatment_amount"
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

    trade_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    tax_type: Mapped[TaxType] = mapped_column(
        SQLEnum(
            TaxType,
            name="tax_type_enum",
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    direction: Mapped[TaxDirection] = mapped_column(
        SQLEnum(
            TaxDirection,
            name="tax_direction_enum",
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    tax_rate_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=9,
            scale=6,
        ),
        nullable=False,
    )

    treatment: Mapped[TaxTreatment] = mapped_column(
        SQLEnum(
            TaxTreatment,
            name="tax_treatment_enum",
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    recognition_method: Mapped[
        TaxRecognitionMethod
    ] = mapped_column(
        SQLEnum(
            TaxRecognitionMethod,
            name=(
                "tax_recognition_method_enum"
            ),
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    calculation_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        server_default=func.now(),
        nullable=False,
    )
