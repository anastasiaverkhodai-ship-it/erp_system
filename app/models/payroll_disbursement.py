from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


import sqlalchemy as sa

class PayrollDisbursement(Base):
    __tablename__ = "payroll_disbursements"

    __table_args__ = (
        CheckConstraint("withheld_amount >= 0 AND withheld_amount <> 'NaN'::numeric", name="ck_payroll_disbursement_withheld"),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_disbursements_company_id_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "cash_desk_id",
            name="uq_payroll_disbursements_company_id_cash_desk",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_payroll_disbursements_company_request",
        ),
        CheckConstraint("(cancelled_at IS NULL) = (cancelled_by IS NULL)",
                        name="ck_payroll_disbursement_cancellation"),
        CheckConstraint("reversed_on IS NULL OR confirmed_at IS NOT NULL",
                        name="ck_payroll_disbursement_chronology"),
        CheckConstraint("amount > 0", name="ck_payroll_disbursement_positive"),
        CheckConstraint(
            "num_nonnulls(bank_account_id, cash_desk_id) = 1",
            name="ck_payroll_disbursement_payment_source",
        ),
        CheckConstraint(
            "(bank_account_id IS NOT NULL AND employee_iban_snapshot IS NOT NULL) "
            "OR (cash_desk_id IS NOT NULL AND employee_iban_snapshot IS NULL)",
            name="ck_payroll_disbursement_payment_identity",
        ),
        CheckConstraint("num_nonnulls(payroll_calculation_id, opening_debt_id) = 1",
                        name="ck_payroll_disbursements_source"),
        ForeignKeyConstraint(["company_id", "opening_debt_id"],
                             ["payroll_opening_debts.company_id", "payroll_opening_debts.id"],
                             name="fk_payroll_disbursements_opening_debt", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_disbursements_company_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            [
                "employees.company_id",
                "employees.id",
            ],
            name="fk_payroll_disbursements_company_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            [
                "bank_accounts.company_id",
                "bank_accounts.id",
            ],
            name="fk_payroll_disbursements_company_bank_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_payroll_disbursements_company_cash_desk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "cash_account_id_snapshot"],
            ["accounts.company_id", "accounts.id"],
            name="fk_payroll_disbursements_cash_account_snapshot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(cash_desk_id IS NULL AND cash_account_id_snapshot IS NULL) "
            "OR (cash_desk_id IS NOT NULL "
            "AND cash_account_id_snapshot IS NOT NULL)",
            name="ck_payroll_disbursement_cash_account_snapshot",
        ),
        Index(
            "ix_payroll_disbursements_company_payment_date",
            "company_id",
            "payment_date",
        ),
    )

    withheld_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, server_default="0")

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)

    payroll_calculation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opening_debt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    bank_account_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    cash_desk_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    cash_account_id_snapshot: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    employee_iban_snapshot: Mapped[str | None] = mapped_column(
        String(34),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    payment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# Payroll-specific cash issue/reversal evidence.
# Financial posting remains owned by PayrollDisbursement.
# Never create a generic Payment for this document.


class PayrollCashDocument(Base):
    __tablename__ = "payroll_cash_documents"

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_payroll_cash_docs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_cash_docs_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_payroll_cash_docs_company_desk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "payroll_disbursement_id",
                "cash_desk_id",
            ],
            [
                "payroll_disbursements.company_id",
                "payroll_disbursements.id",
                "payroll_disbursements.cash_desk_id",
            ],
            name="fk_payroll_cash_docs_payout_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "id",
            "cash_desk_id",
            "payroll_disbursement_id",
            name="uq_payroll_cash_docs_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "cash_desk_id",
                "payroll_disbursement_id",
            ],
            [
                "payroll_cash_documents.company_id",
                "payroll_cash_documents.id",
                "payroll_cash_documents.cash_desk_id",
                "payroll_cash_documents.payroll_disbursement_id",
            ],
            name="fk_payroll_cash_docs_reversal_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id",
            "document_number",
            name="uq_payroll_cash_docs_number",
        ),
        sa.UniqueConstraint(
            "reversal_of_id",
            name="uq_payroll_cash_docs_single_reversal",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount <> 'NaN'::numeric",
            name="ck_payroll_cash_docs_positive_amount",
        ),
        sa.CheckConstraint(
            "event_kind IN ('issue', 'reversal')",
            name="ck_payroll_cash_docs_event_kind",
        ),
        sa.CheckConstraint(
            "(event_kind = 'issue' AND reversal_of_id IS NULL)"
            " OR (event_kind = 'reversal' "
            "AND reversal_of_id IS NOT NULL)",
            name="ck_payroll_cash_docs_event_identity",
        ),
        sa.CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_payroll_cash_docs_not_self_reversal",
        ),
        sa.CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_payroll_cash_docs_number_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_payroll_cash_docs_currency_length",
        ),
        sa.Index(
            "ix_payroll_cash_docs_company_date_desk",
            "company_id",
            "document_date",
            "cash_desk_id",
        ),
        sa.Index(
            "uq_payroll_cash_docs_original_payout",
            "company_id",
            "payroll_disbursement_id",
            unique=True,
            postgresql_where=sa.text("event_kind = 'issue'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.Integer, primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    cash_desk_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    payroll_disbursement_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    document_number: Mapped[str] = mapped_column(
        sa.String(100), nullable=False
    )
    event_kind: Mapped[str] = mapped_column(
        sa.String(20), nullable=False
    )
    document_date: Mapped[date] = mapped_column(
        sa.Date, nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        sa.Numeric(18, 2), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        sa.String(3), nullable=False
    )
    created_by: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    reversal_of_id: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


class CashDeskOpeningEvidence(Base):
    """Immutable opening declaration, not automatically verified funds."""

    __tablename__ = "cash_desk_opening_evidence"

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_desk_open_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_cash_desk_open_desk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attested_by"],
            ["users.id"],
            name="fk_cash_desk_open_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "company_id", "id",
            name="uq_cash_desk_open_company_id",
        ),
        sa.UniqueConstraint(
            "company_id",
            "cash_desk_id",
            name="uq_cash_desk_open_single_baseline",
        ),
        sa.CheckConstraint(
            "declared_amount >= 0 "
            "AND declared_amount <> 'NaN'::numeric",
            name="ck_cash_desk_open_amount",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cash_desk_open_currency",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_reference)) > 0",
            name="ck_cash_desk_open_evidence_reference",
        ),
        sa.Index(
            "ix_cash_desk_open_company_effective_date",
            "company_id",
            "effective_date",
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.Integer, primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    cash_desk_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    effective_date: Mapped[date] = mapped_column(
        sa.Date, nullable=False
    )
    declared_amount: Mapped[Decimal] = mapped_column(
        sa.Numeric(18, 2), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        sa.String(3), nullable=False
    )
    evidence_reference: Mapped[str] = mapped_column(
        sa.String(255), nullable=False
    )
    attested_by: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class CashDeskOpeningReview(Base):
    """Immutable human review of a declared physical cash opening."""

    __tablename__ = "cash_desk_opening_reviews"

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_cash_open_review_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "opening_evidence_id"],
            [
                "cash_desk_opening_evidence.company_id",
                "cash_desk_opening_evidence.id",
            ],
            name="fk_cash_open_review_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_cash_open_review_actor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "opening_evidence_id",
            name="uq_cash_open_review_once",
        ),
        sa.CheckConstraint(
            "length(trim(review_reference)) > 0",
            name="ck_cash_open_review_reference",
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.Integer, primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    opening_evidence_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    reviewed_by: Mapped[int] = mapped_column(
        sa.Integer, nullable=False
    )
    review_reference: Mapped[str] = mapped_column(
        sa.String(255), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
