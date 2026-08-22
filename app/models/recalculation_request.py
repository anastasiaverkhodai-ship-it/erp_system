from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.recalculation_types import (
    RecalculationDomain,
    RecalculationStatus,
)


class RecalculationRequest(Base):
    """
    Persistent backdated recalculation request.

    Multiple requests may exist for the same logical
    stream. They can later be coalesced into one
    recalculation window.
    """

    __tablename__ = "recalculation_requests"

    __table_args__ = (
        Index(
            "ix_recalculation_requests_stream_status_effective",
            "company_id",
            "domain",
            "stream_key",
            "status",
            "effective_from",
        ),
        Index(
            "ix_recalculation_requests_status_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    request_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    domain: Mapped[RecalculationDomain] = mapped_column(
        SQLEnum(
            RecalculationDomain,
            name="recalculation_domain_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
        index=True,
    )

    stream_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    effective_from: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
    )

    status: Mapped[RecalculationStatus] = mapped_column(
        SQLEnum(
            RecalculationStatus,
            name="recalculation_status_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        default=RecalculationStatus.PENDING,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    claimed_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
