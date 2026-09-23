from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class YearEndClosing(Base):
    """Lifecycle only; all financial amounts remain in the canonical journal."""
    __tablename__ = "year_end_closings"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_year_end_company_id"),
        UniqueConstraint("company_id", "request_key", name="uq_year_end_request"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="ck_year_end_year"),
        CheckConstraint("status IN ('closed', 'reversed')", name="ck_year_end_status"),
        Index("uq_year_end_active", "company_id", "year", unique=True,
              postgresql_where=text("status = 'closed'")),
        ForeignKeyConstraint(["company_id", "journal_entry_id"],
            ["journal_entries.company_id", "journal_entries.id"],
            name="fk_year_end_company_journal", ondelete="RESTRICT"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="closed")
    request_key: Mapped[str] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    preview_fingerprint: Mapped[str] = mapped_column(String(64))
    journal_entry_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
