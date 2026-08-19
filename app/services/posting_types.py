from decimal import Decimal

from app.models.inventory_cost_entry import (
    InventoryCostEntry,
)


type StockDeltas = dict[
    tuple[int, int],
    Decimal,
]

type InventoryCosts = dict[
    int,
    InventoryCostEntry,
]