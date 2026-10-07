from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollRegulatoryReportKind(str, Enum):
    D1 = "d1"
    FOUR_DF = "4df"
    D5 = "d5"
    D6 = "d6"


class PayrollRegulatoryReportStatus(str, Enum):
    DRAFT = "draft"
    GENERATED = "generated"


class PayrollRegulatoryReport(Base):
    __tablename__ = "payroll_regulatory_reports"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_regulatory_reports_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_period_id",
            "report_kind",
            "revision",
            name="uq_payroll_regulatory_reports_period_kind_revision",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            ["payroll_periods.company_id", "payroll_periods.id"],
            name="fk_payroll_regulatory_reports_company_period",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "report_kind IN ('d1', '4df', 'd5', 'd6')",
            name="ck_payroll_regulatory_reports_kind",
        ),
        CheckConstraint(
            "status IN ('draft', 'generated')",
            name="ck_payroll_regulatory_reports_status",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_payroll_regulatory_reports_revision_positive",
        ),
        CheckConstraint(
            """
            (
                status = 'draft'
                AND generated_at IS NULL
                AND generated_by IS NULL
            )
            OR
            (
                status = 'generated'
                AND generated_at IS NOT NULL
                AND generated_by IS NOT NULL
            )
            """,
            name="ck_payroll_regulatory_reports_generation_metadata",
        ),
        Index(
            "ix_payroll_regulatory_reports_company_period",
            "company_id",
            "payroll_period_id",
        ),
        Index(
            "ix_payroll_regulatory_reports_company_kind_status",
            "company_id",
            "report_kind",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payroll_period_id: Mapped[int] = mapped_column(Integer, nullable=False)

    report_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=PayrollRegulatoryReportStatus.DRAFT.value,
        server_default=PayrollRegulatoryReportStatus.DRAFT.value,
    )

    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    generated_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )


class PayrollRegulatoryReportRow(Base):
    __tablename__ = "payroll_regulatory_report_rows"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_regulatory_report_rows_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_regulatory_report_id",
            "row_no",
            name="uq_payroll_regulatory_report_rows_report_row_no",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_regulatory_report_id"],
            [
                "payroll_regulatory_reports.company_id",
                "payroll_regulatory_reports.id",
            ],
            name="fk_payroll_regulatory_report_rows_company_report",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "row_no > 0",
            name="ck_payroll_regulatory_report_rows_row_no_positive",
        ),
        CheckConstraint(
            "employee_id IS NOT NULL OR employment_contract_id IS NOT NULL",
            name="ck_payroll_regulatory_report_rows_source_present",
        ),
        Index(
            "ix_payroll_regulatory_report_rows_company_report",
            "company_id",
            "payroll_regulatory_report_id",
        ),
        Index(
            "ix_payroll_regulatory_report_rows_company_employee",
            "company_id",
            "employee_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payroll_regulatory_report_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    row_no: Mapped[int] = mapped_column(Integer, nullable=False)

    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employment_contract_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    payroll_calculation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    row_code: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
