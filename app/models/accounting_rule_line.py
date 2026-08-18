from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AccountingRuleSide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountingAmountSource(str, Enum):
    DOCUMENT_TOTAL = "document_total"
    LINE_TOTAL = "line_total"


class AccountingRuleLine(Base):
    __tablename__ = "accounting_rule_lines"

    __table_args__ = (
        UniqueConstraint(
            "accounting_rule_id",
            "line_no",
            name="uq_accounting_rule_line_number",
        ),
        CheckConstraint(
            "line_no > 0",
            name="ck_accounting_rule_line_number_positive",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    accounting_rule_id: Mapped[int] = mapped_column(
        ForeignKey(
            "accounting_rules.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    line_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    side: Mapped[AccountingRuleSide] = mapped_column(
        SQLEnum(
            AccountingRuleSide,
            name="accounting_rule_side_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
    )

    amount_source: Mapped[AccountingAmountSource] = mapped_column(
        SQLEnum(
            AccountingAmountSource,
            name="accounting_amount_source_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    accounting_rule = relationship(
        "AccountingRule",
        back_populates="lines",
    )