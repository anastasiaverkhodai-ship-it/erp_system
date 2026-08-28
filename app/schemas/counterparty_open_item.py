from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
)

from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)


class CounterpartyOpenItemResponse(BaseModel):
    """
    Persistent AR/AP obligation.

    open_amount is deliberately absent until persistent settlement
    allocations exist. original_amount is the immutable invoice
    obligation amount.
    """

    model_config = ConfigDict(
        from_attributes=True,
    )

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

    created_at: datetime
