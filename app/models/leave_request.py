import enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
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


class LeaveType(str, enum.Enum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"
    OTHER = "other"


class LeaveRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_leave_requests_company_id_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_leave_requests_company_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "end_date >= start_date",
            name="ck_leave_requests_date_range",
        ),
        CheckConstraint(
            "leave_type IN ('annual','sick','unpaid','other')",
            name="ck_leave_requests_type",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled')",
            name="ck_leave_requests_status",
        ),
        CheckConstraint(
            """
            (
                status = 'pending'
                AND approved_by IS NULL
                AND approved_at IS NULL
                AND rejected_by IS NULL
                AND rejected_at IS NULL
                AND cancelled_by IS NULL
                AND cancelled_at IS NULL
            )
            OR
            (
                status = 'approved'
                AND approved_by IS NOT NULL
                AND approved_at IS NOT NULL
                AND rejected_by IS NULL
                AND rejected_at IS NULL
                AND cancelled_by IS NULL
                AND cancelled_at IS NULL
            )
            OR
            (
                status = 'rejected'
                AND approved_by IS NULL
                AND approved_at IS NULL
                AND rejected_by IS NOT NULL
                AND rejected_at IS NOT NULL
                AND cancelled_by IS NULL
                AND cancelled_at IS NULL
            )
            OR
            (
                status = 'cancelled'
                AND cancelled_by IS NOT NULL
                AND cancelled_at IS NOT NULL
                AND rejected_by IS NULL
                AND rejected_at IS NULL
            )
            """,
            name="ck_leave_requests_lifecycle_metadata",
        ),
        Index(
            "ix_leave_requests_company_contract_dates",
            "company_id",
            "employment_contract_id",
            "start_date",
            "end_date",
        ),
        Index(
            "ix_leave_requests_company_status",
            "company_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    leave_type: Mapped[LeaveType] = mapped_column(
        SAEnum(
            LeaveType,
            name="leave_type",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
    )

    start_date: Mapped[object] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[object] = mapped_column(
        Date,
        nullable=False,
    )

    status: Mapped[LeaveRequestStatus] = mapped_column(
        SAEnum(
            LeaveRequestStatus,
            name="leave_request_status",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=LeaveRequestStatus.PENDING,
        server_default=LeaveRequestStatus.PENDING.value,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    requested_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    approved_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejected_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    rejected_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    cancelled_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
