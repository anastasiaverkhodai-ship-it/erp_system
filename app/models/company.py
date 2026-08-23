from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)

class InventoryValuationMethod(str, Enum):
    FIFO = "fifo"
    WEIGHTED_AVERAGE_MOVING = "weighted_average_moving"
    WEIGHTED_AVERAGE_PERIODIC = "weighted_average_periodic"
    IDENTIFIED_COST = "identified_cost"
    STANDARD_COST = "standard_cost"
    RETAIL_PRICE = "retail_price"

class Company(Base):
    __tablename__ = "companies"

    __table_args__ = (
        CheckConstraint(
            (
                "chart_of_accounts_template IN ("
                "'general_291', "
                "'simplified_186'"
                ")"
            ),
            name="chart_of_accounts_template_enum",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    edrpou: Mapped[str | None] = mapped_column(
        String(8),
        unique=True,
        nullable=True,
    )

    vat_number: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
    )

    inventory_valuation_method: Mapped[
        InventoryValuationMethod
    ] = mapped_column(
        SQLEnum(
            InventoryValuationMethod,
            name="inventory_valuation_method_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        default=InventoryValuationMethod.FIFO,
        server_default=InventoryValuationMethod.FIFO.value,
        nullable=False,
    )

    chart_of_accounts_template: Mapped[
        ChartOfAccountsTemplateType
    ] = mapped_column(
        SQLEnum(
            ChartOfAccountsTemplateType,
            name="chart_of_accounts_template_enum",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=32,
        ),
        default=ChartOfAccountsTemplateType.GENERAL_291,
        server_default=(
            ChartOfAccountsTemplateType.GENERAL_291.value
        ),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )