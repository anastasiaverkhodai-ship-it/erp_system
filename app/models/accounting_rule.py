from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum as SQLEnum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.document import DocumentType


class AccountingRule(Base):
    __tablename__ = "accounting_rules"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "code",
            name="uq_accounting_rule_company_code",
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

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    document_type: Mapped[DocumentType] = mapped_column(
        SQLEnum(
            DocumentType,
            name="accounting_rule_document_type_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        nullable=False,
    )

    lines = relationship(
        "AccountingRuleLine",
        back_populates="accounting_rule",
        cascade="all, delete-orphan",
    )