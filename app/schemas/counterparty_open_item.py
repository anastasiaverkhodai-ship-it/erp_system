from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)


class CounterpartyOpenItemResponse(BaseModel):
    """
    Persistent AR/AP obligation enriched with
    settlement-derived balances.

    original_amount remains immutable persistence.

    settled_amount and open_amount are read-model
    values derived from ACTIVE settlement allocations.

    For CANCELLED obligations open_amount is zero.
    """

    id: int
    company_id: int
    trade_document_id: int
    counterparty_id: int
    contract_id: int | None

    item_type: CounterpartyOpenItemType
    status: CounterpartyOpenItemStatus

    document_date: date
    due_date: date

    currency_code: str

    original_amount: Decimal
    settled_amount: Decimal
    open_amount: Decimal

    created_at: datetime
