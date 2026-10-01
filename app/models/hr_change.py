"""Append-only application audit of HR master data, separate from payroll events."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class HRChange(Base):
    __tablename__ = 'hr_changes'
    __table_args__ = (
        CheckConstraint("entity_type IN ('employee','department','position','employment_contract','salary_rate')", name='ck_hr_changes_entity_type'),
        Index('ix_hr_changes_entity', 'company_id', 'entity_type', 'entity_id', 'id'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id', ondelete='RESTRICT'), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_state: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    after_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Null means a system/service call without an authenticated actor, never an invented user.
    changed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
