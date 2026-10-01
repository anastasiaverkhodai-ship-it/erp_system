from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Department(Base):
    __tablename__ = "departments"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_departments_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "code",
            name="uq_departments_company_code",
        ),
        ForeignKeyConstraint(
            ["company_id", "parent_id"],
            ["departments.company_id", "departments.id"],
            name="fk_departments_company_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_departments_code_nonempty",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_departments_name_nonempty",
        ),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_departments_not_self_parent",
        ),
        Index(
            "ix_departments_company_active",
            "company_id",
            "is_active",
        ),
        Index(
            "ix_departments_company_parent",
            "company_id",
            "parent_id",
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

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
