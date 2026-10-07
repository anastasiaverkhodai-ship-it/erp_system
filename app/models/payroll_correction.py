from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollCorrection(Base):
    """
    Append-only linkage between an original finalized payroll calculation
    and the replacement calculation created by an explicit correction.

    The original calculation is never silently mutated.
    """

    __tablename__ = "payroll_corrections"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_corrections_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_payroll_corrections_company_request",
        ),
        UniqueConstraint(
            "company_id",
            "replacement_payroll_calculation_id",
            name="uq_payroll_corrections_company_replacement",
        ),
        ForeignKeyConstraint(
            ["company_id", "original_payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_corrections_company_original_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "replacement_payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_corrections_company_replacement_calculation",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_payroll_corrections_company_original",
            "company_id",
            "original_payroll_calculation_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    original_payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    replacement_payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
