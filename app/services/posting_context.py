from dataclasses import dataclass, field
from decimal import Decimal
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
from app.models.journal_entry import JournalEntry

@dataclass(slots=True)
class PostingContext:
    db: AsyncSession
    document: Document
    operation_date: date
    posting_time: datetime
    accounting_rule_id: int
    created_by: int
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

    @property
    def user_id(self) -> int:
        return self.created_by

    @property
    def rule_id(self) -> int:
        return self.accounting_rule_id

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

    def add_pvc_ma_issue_reconciliation(
        self,
        result: object,
    ) -> None:
        current = list(
            self.get_pvc_ma_issue_reconciliations()
        )
        current.append(
            result
        )
        self.shared_data[
            PostingContextKey.PVC_MA_ISSUE_RECONCILIATIONS
        ] = tuple(
            current
        )

    def get_pvc_ma_issue_reconciliations(
        self,
    ) -> tuple[object, ...]:
        value = self.shared_data.get(
            PostingContextKey.PVC_MA_ISSUE_RECONCILIATIONS
        )

        if value is None:
            return ()

        if not isinstance(
            value,
            tuple,
        ):
            raise TypeError(
                "Posting context PVC MA reconciliation "
                "data has invalid type"
            )

        return value

    def set_receipt_exact_valuation_amount(
        self,
        document_line_id: int,
        valuation_amount: Decimal,
    ) -> None:
        values = dict(
            self.shared_data.get(
                PostingContextKey.RECEIPT_EXACT_VALUATION_AMOUNTS,
                {},
            )
        )

        values[
            document_line_id
        ] = Decimal(
            valuation_amount
        )

        self.shared_data[
            PostingContextKey.RECEIPT_EXACT_VALUATION_AMOUNTS
        ] = values

    def get_receipt_exact_valuation_amount(
        self,
        document_line_id: int,
    ) -> Decimal | None:
        values = self.shared_data.get(
            PostingContextKey.RECEIPT_EXACT_VALUATION_AMOUNTS
        )

        if values is None:
            return None

        if not isinstance(
            values,
            dict,
        ):
            raise TypeError(
                "Posting context receipt exact valuation "
                "data has invalid type"
            )

        value = values.get(
            document_line_id
        )

        if value is None:
            return None

        return Decimal(value)

    def set_journal_entry(
        self,
        journal_entry: JournalEntry,
    ) -> None:
        self.shared_data[
            PostingContextKey.JOURNAL_ENTRY
        ] = journal_entry

    def get_journal_entry(
        self,
    ) -> JournalEntry | None:
        value = self.shared_data.get(
            PostingContextKey.JOURNAL_ENTRY
        )

        if value is None:
            return None

        if not isinstance(
            value,
            JournalEntry,
        ):
            raise TypeError(
                "Posting context journal entry "
                "has invalid type"
            )

        return value

def create_posting_context(
    db: AsyncSession,
    document: Document,
    accounting_rule_id: int,
    created_by: int,
) -> PostingContext:
    return PostingContext(
        db=db,
        document=document,
        operation_date=document.document_date,
        posting_time=datetime.now(
            timezone.utc
        ).replace(tzinfo=None),
        accounting_rule_id=accounting_rule_id,
        created_by=created_by,
    )