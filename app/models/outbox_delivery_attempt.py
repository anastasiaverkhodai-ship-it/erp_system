from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.outbox_delivery_attempt_types import (
    OutboxDeliveryAttemptStatus,
)


class OutboxDeliveryAttempt(Base):
    __tablename__ = "outbox_delivery_attempts"

    __table_args__ = (
        UniqueConstraint(
            "outbox_event_id",
            "attempt_number",
            name="uq_outbox_delivery_attempt_event_number",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    outbox_event_id: Mapped[int] = mapped_column(
        ForeignKey(
            "outbox_events.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[OutboxDeliveryAttemptStatus] = mapped_column(
        SQLEnum(
            OutboxDeliveryAttemptStatus,
            name="outbox_delivery_attempt_status_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )