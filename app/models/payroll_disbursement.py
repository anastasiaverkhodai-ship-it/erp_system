from datetime import date, datetime
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


class PayrollDisbursement(Base):
    __tablename__ = "payroll_disbursements"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_disbursements_company_id_id",
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
        Index(
            "ix_payroll_disbursements_company_payment_date",
            "company_id",
            "payment_date",
        ),
    )

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

    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    bank_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    employee_iban_snapshot: Mapped[str] = mapped_column(
        String(34),
        nullable=False,
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
        default=datetime.utcnow,
    )
