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


# -------------------------------------------------------------------
# Phase 13.14 — Immutable D5/D6 review snapshot persistence.
#
# This table is registered on the same SQLAlchemy metadata as the
# existing payroll regulatory report model. It is deliberately not
# mapped to an official statutory report-row category.
#
# The matching Alembic revision creates the PostgreSQL append-only
# protection trigger. The ORM table declaration is required so that
# Alembic autogenerate does not mistake the table for an extra object.
# -------------------------------------------------------------------

from sqlalchemy import (
    Table as _HRReviewTable,
    Column as _HRReviewColumn,
    Integer as _HRReviewInteger,
    String as _HRReviewString,
    Text as _HRReviewText,
    DateTime as _HRReviewDateTime,
    ForeignKeyConstraint as _HRReviewForeignKeyConstraint,
    CheckConstraint as _HRReviewCheckConstraint,
    UniqueConstraint as _HRReviewUniqueConstraint,
    Index as _HRReviewIndex,
    func as _hr_review_func,
)


payroll_hr_review_snapshots = _HRReviewTable(
    "payroll_hr_review_snapshots",
    PayrollRegulatoryReport.__table__.metadata,

    _HRReviewColumn(
        "id",
        _HRReviewInteger(),
        primary_key=True,
    ),

    _HRReviewColumn(
        "company_id",
        _HRReviewInteger(),
        nullable=False,
    ),

    _HRReviewColumn(
        "payroll_period_id",
        _HRReviewInteger(),
        nullable=False,
    ),

    _HRReviewColumn(
        "payroll_regulatory_report_id",
        _HRReviewInteger(),
        nullable=False,
    ),

    _HRReviewColumn(
        "report_kind",
        _HRReviewString(16),
        nullable=False,
    ),

    _HRReviewColumn(
        "report_revision",
        _HRReviewInteger(),
        nullable=False,
    ),

    _HRReviewColumn(
        "previous_snapshot_id",
        _HRReviewInteger(),
        nullable=True,
    ),

    _HRReviewColumn(
        "payload_json",
        _HRReviewText(),
        nullable=False,
    ),

    _HRReviewColumn(
        "snapshot_sha256",
        _HRReviewString(64),
        nullable=False,
    ),

    _HRReviewColumn(
        "created_at",
        _HRReviewDateTime(timezone=True),
        nullable=False,
        server_default=_hr_review_func.now(),
    ),

    _HRReviewForeignKeyConstraint(
        [
            "company_id",
            "payroll_regulatory_report_id",
        ],
        [
            "payroll_regulatory_reports.company_id",
            "payroll_regulatory_reports.id",
        ],
        ondelete="RESTRICT",
    ),

    _HRReviewForeignKeyConstraint(
        [
            "company_id",
            "payroll_period_id",
        ],
        [
            "payroll_periods.company_id",
            "payroll_periods.id",
        ],
        ondelete="RESTRICT",
    ),

    _HRReviewForeignKeyConstraint(
        [
            "company_id",
            "previous_snapshot_id",
        ],
        [
            "payroll_hr_review_snapshots.company_id",
            "payroll_hr_review_snapshots.id",
        ],
        name="fk_payroll_hr_review_snapshot_previous",
        ondelete="RESTRICT",
    ),

    _HRReviewCheckConstraint(
        "report_kind IN ('d5', 'd6')",
        name="ck_payroll_hr_review_snapshot_kind",
    ),

    _HRReviewCheckConstraint(
        "report_revision > 0",
        name="ck_payroll_hr_review_snapshot_revision",
    ),

    _HRReviewCheckConstraint(
        "snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_payroll_hr_review_snapshot_hash",
    ),

    _HRReviewUniqueConstraint(
        "company_id",
        "id",
        name="uq_payroll_hr_review_snapshot_company_id",
    ),

    _HRReviewUniqueConstraint(
        "company_id",
        "payroll_regulatory_report_id",
        name="uq_payroll_hr_review_snapshot_report",
    ),
)


_HRReviewIndex(
    "ix_payroll_hr_review_snapshot_period_kind",
    payroll_hr_review_snapshots.c.company_id,
    payroll_hr_review_snapshots.c.payroll_period_id,
    payroll_hr_review_snapshots.c.report_kind,
    payroll_hr_review_snapshots.c.report_revision,
)


# _HR_REVIEW_IMMUTABILITY_DDL_V1
#
# Alembic creates the trigger in a migrated database.
# SQLAlchemy create_all() is also used by isolated test fixtures.
# Without these hooks, a fixture-created table can be missing
# the protection trigger even though Alembic migration tests pass.
#
# The trigger is deliberately installed at database level.
# No application-side flag can bypass it.

from sqlalchemy import DDL as _HRReviewDDL
from sqlalchemy import event as _HRReviewEvent


_HR_REVIEW_PROTECT_FUNCTION_DDL = _HRReviewDDL(
    """
    CREATE OR REPLACE FUNCTION protect_payroll_hr_review_snapshot()
    RETURNS trigger AS $hr_review_immutable$
    BEGIN
        RAISE EXCEPTION
            'Payroll HR review snapshots are append-only'
            USING ERRCODE = 'P0001';
    END;
    $hr_review_immutable$ LANGUAGE plpgsql
    """
).execute_if(dialect="postgresql")


_HR_REVIEW_PROTECT_TRIGGER_DDL = _HRReviewDDL(
    """
    CREATE TRIGGER trg_payroll_hr_review_snapshot_immutable
    BEFORE UPDATE OR DELETE
    ON payroll_hr_review_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION protect_payroll_hr_review_snapshot()
    """
).execute_if(dialect="postgresql")


_HRReviewEvent.listen(
    payroll_hr_review_snapshots,
    "after_create",
    _HR_REVIEW_PROTECT_FUNCTION_DDL,
)

_HRReviewEvent.listen(
    payroll_hr_review_snapshots,
    "after_create",
    _HR_REVIEW_PROTECT_TRIGGER_DDL,
)


# PHASE13_REGULATORY_INTERNAL_SNAPSHOT_METADATA_V1
#
# This table registers the already-defined Alembic schema with
# SQLAlchemy metadata. It does not perform DDL or authorize
# official report generation.
#
# Keep this registration in the existing regulatory model module
# so Alembic's established model-import path sees the table.

from sqlalchemy import (
    Table as _reg_table,
    Column as _reg_column,
    BigInteger as _reg_bigint,
    Integer as _reg_integer,
    String as _reg_string,
    Text as _reg_text,
    CHAR as _reg_char,
    DateTime as _reg_datetime,
    Identity as _reg_identity,
    ForeignKeyConstraint as _reg_fk,
    UniqueConstraint as _reg_unique,
    CheckConstraint as _reg_check,
    text as _reg_text_expression,
)


payroll_regulatory_internal_snapshots_table = _reg_table(
    "payroll_regulatory_internal_snapshots",
    PayrollRegulatoryReport.metadata,

    _reg_column(
        "id",
        _reg_bigint,
        _reg_identity(always=False),
        primary_key=True,
    ),

    _reg_column(
        "company_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "payroll_period_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "version",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "request_key",
        _reg_string(120),
        nullable=False,
    ),

    _reg_column(
        "d1_report_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "fourdf_report_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "d5_report_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "d6_report_id",
        _reg_integer,
        nullable=False,
    ),

    _reg_column(
        "previous_snapshot_id",
        _reg_bigint,
        nullable=True,
    ),

    _reg_column(
        "payload_json",
        _reg_text,
        nullable=False,
    ),

    _reg_column(
        "snapshot_sha256",
        _reg_char(64),
        nullable=False,
    ),

    _reg_column(
        "status",
        _reg_string(40),
        nullable=False,
        server_default=_reg_text_expression(
            "'internal_review_only'"
        ),
    ),

    _reg_column(
        "created_at",
        _reg_datetime(timezone=True),
        nullable=False,
        server_default=_reg_text_expression(
            "CURRENT_TIMESTAMP"
        ),
    ),

    _reg_unique(
        "company_id",
        "id",
        name="uq_regint_snapshot_company_id",
    ),

    _reg_unique(
        "company_id",
        "payroll_period_id",
        "version",
        name="uq_regint_snapshot_revision",
    ),

    _reg_unique(
        "company_id",
        "payroll_period_id",
        "request_key",
        name="uq_regint_snapshot_request",
    ),

    _reg_fk(
        ["company_id", "payroll_period_id"],
        ["payroll_periods.company_id", "payroll_periods.id"],
        name="fk_regint_period",
        ondelete="RESTRICT",
    ),

    _reg_fk(
        ["company_id", "d1_report_id"],
        [
            "payroll_regulatory_reports.company_id",
            "payroll_regulatory_reports.id",
        ],
        name="fk_regint_d1",
        ondelete="RESTRICT",
    ),

    _reg_fk(
        ["company_id", "fourdf_report_id"],
        [
            "payroll_regulatory_reports.company_id",
            "payroll_regulatory_reports.id",
        ],
        name="fk_regint_4df",
        ondelete="RESTRICT",
    ),

    _reg_fk(
        ["company_id", "d5_report_id"],
        [
            "payroll_regulatory_reports.company_id",
            "payroll_regulatory_reports.id",
        ],
        name="fk_regint_d5",
        ondelete="RESTRICT",
    ),

    _reg_fk(
        ["company_id", "d6_report_id"],
        [
            "payroll_regulatory_reports.company_id",
            "payroll_regulatory_reports.id",
        ],
        name="fk_regint_d6",
        ondelete="RESTRICT",
    ),

    _reg_fk(
        ["company_id", "previous_snapshot_id"],
        [
            "payroll_regulatory_internal_snapshots.company_id",
            "payroll_regulatory_internal_snapshots.id",
        ],
        name="fk_regint_previous",
        ondelete="RESTRICT",
    ),

    _reg_check(
        "version > 0",
        name="ck_regint_version",
    ),

    _reg_check(
        "length(request_key) BETWEEN 8 AND 120",
        name="ck_regint_request",
    ),

    _reg_check(
        "snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_regint_digest",
    ),

    _reg_check(
        "status = 'internal_review_only'",
        name="ck_regint_status",
    ),

    _reg_check(
        """
        d1_report_id <> fourdf_report_id
        AND d1_report_id <> d5_report_id
        AND d1_report_id <> d6_report_id
        AND fourdf_report_id <> d5_report_id
        AND fourdf_report_id <> d6_report_id
        AND d5_report_id <> d6_report_id
        """,
        name="ck_regint_distinct_reports",
    ),

    _reg_check(
        """
        (version = 1 AND previous_snapshot_id IS NULL)
        OR
        (version > 1 AND previous_snapshot_id IS NOT NULL)
        """,
        name="ck_regint_chain",
    ),
)
