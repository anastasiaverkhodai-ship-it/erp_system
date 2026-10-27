"""Evidence-backed advance tax remittance records."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey,
    ForeignKeyConstraint, Integer, Numeric,
    String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollAdvanceTaxRemittance(Base):
    __tablename__ = "payroll_advance_tax_remittances"

    __table_args__ = (
        UniqueConstraint(
            "company_id", "id",
            name="uq_advance_tax_remittance_company_id",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_advance_tax_remittance_request",
        ),
        UniqueConstraint(
            "company_id", "journal_entry_id",
            name="uq_advance_tax_remittance_journal",
        ),
        UniqueConstraint(
            "company_id", "payroll_advance_id", "component",
            name="uq_advance_tax_remittance_component",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_advance_id"],
            ["payroll_advances.company_id", "payroll_advances.id"],
            name="fk_advance_tax_remittance_advance",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_advance_tax_remittance_journal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_advance_tax_remittance_bank",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "component IN ('personal_income_tax', 'military_levy')",
            name="ck_advance_tax_remittance_component",
        ),
        CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_advance_tax_remittance_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payroll_advance_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    journal_entry_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    bank_account_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    component: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    request_key: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    document_reference: Mapped[str] = mapped_column(
        String(500), nullable=False
    )
    budget_payment_reference: Mapped[str] = mapped_column(
        String(500), nullable=False
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
