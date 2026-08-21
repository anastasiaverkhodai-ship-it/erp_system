from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdempotencyResult(Base):
    __tablename__ = "idempotency_results"

    __table_args__ = (
        UniqueConstraint(
            "idempotency_record_id",
            name="uq_idempotency_result_record",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    idempotency_record_id: Mapped[int] = mapped_column(
        ForeignKey(
            "idempotency_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    result_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    result_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    result_payload: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )