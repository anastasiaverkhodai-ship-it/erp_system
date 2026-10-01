import json
from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.services.hr_change_service import snapshot


class HistoryDecimalTestBase(DeclarativeBase):
    pass


class HistoryDecimalRow(HistoryDecimalTestBase):
    __tablename__ = "_history_decimal_regression"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
    )


def test_snapshot_converts_decimal_to_json_safe_string():
    row = HistoryDecimalRow(
        id=1,
        amount=Decimal("5000.25"),
    )

    result = snapshot(row)

    assert result["amount"] == "5000.25"

    json.dumps(result)
