from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentType

from app.services.posting_context_keys import (
    PostingContextKey,
)
from app.services.posting_types import (
    InventoryCosts,
    StockDeltas,
)

from app.models.inventory_cost_entry import (
    InventoryCostEntry,
)

@dataclass(slots=True)
class PostingContext:
    db: AsyncSession
    document: Document
    operation_date: date
    posting_time: datetime
    shared_data: dict[
    PostingContextKey,
    object,
] = field(
    default_factory=dict
)

    @property
    def company_id(self) -> int:
        return self.document.company_id

    @property
    def document_id(self) -> int:
        return self.document.id

    @property
    def document_type(self) -> DocumentType:
        return self.document.document_type

    def set_stock_deltas(
        self,
        stock_deltas: StockDeltas,
    ) -> None:
        self.shared_data[
            PostingContextKey.STOCK_DELTAS
        ] = dict(stock_deltas)

    def get_stock_deltas(
        self,
    ) -> StockDeltas:
        value = self.shared_data.get(
            PostingContextKey.STOCK_DELTAS
        )

        if value is None:
            return {}

        return dict(value)


    def set_inventory_cost(
        self,
        document_line_id: int,
        cost_entry: InventoryCostEntry,
    ) -> None:
        inventory_costs = self.get_inventory_costs()

        inventory_costs[
            document_line_id
        ] = cost_entry

        self.shared_data[
            PostingContextKey.INVENTORY_COSTS
        ] = inventory_costs

    def get_inventory_cost(
        self,
        document_line_id: int,
    ) -> InventoryCostEntry | None:
        inventory_costs = self.get_inventory_costs()

        return inventory_costs.get(
            document_line_id
        )

    def get_inventory_costs(
        self,
    ) -> InventoryCosts:
        value = self.shared_data.get(
            PostingContextKey.INVENTORY_COSTS
        )

        if value is None:
            return {}

        return dict(value)

def create_posting_context(
    db: AsyncSession,
    document: Document,
) -> PostingContext:
    return PostingContext(
        db=db,
        document=document,
        operation_date=document.document_date,
        posting_time=datetime.now(
            timezone.utc
        ).replace(tzinfo=None),
    )