from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Warehouse(Base):
    __tablename__ = "warehouses"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "name",
            name="uq_warehouse_company_name",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_warehouses_company_id_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
