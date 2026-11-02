"""Persisted internal D1/4DF correction snapshot metadata.

This model mirrors migration p13rpt261102a. Its registration prevents
Alembic autogenerate from mistaking the protected snapshot table for
an obsolete table that should be removed.

Official statutory form mapping and filing remain blocked.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    CHAR,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollRegulatoryCorrectionSnapshot(Base):
    __tablename__ = "payroll_regulatory_correction_snapshots"

    __table_args__ = (
        CheckConstraint(
            "report_kind IN ('d1', '4df')",
            name="ck_prcs_report_kind",
        ),
        CheckConstraint(
            "original_calculation_id <> replacement_calculation_id",
            name="ck_prcs_different_revisions",
        ),
        CheckConstraint(
            "projection_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_prcs_digest_format",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_period_id",
            "report_kind",
            "original_calculation_id",
            "replacement_calculation_id",
            name="uq_prcs_correction_snapshot",
        ),
        Index(
            "ix_prcs_company_period",
            "company_id",
            "payroll_period_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=False),
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payroll_period_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    report_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    original_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    replacement_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    schema_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    projection: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    projection_sha256: Mapped[str] = mapped_column(
        CHAR(64),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
