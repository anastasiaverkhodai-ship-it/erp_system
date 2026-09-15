"""Allocate additional value over existing physical provenance, without revaluing it.

FIFO branches follow lot consumption. Moving-average branches use the fraction
of available quantity consumed at each existing movement. Transfers and sales
returns route that fraction into their actual destination receipt. Only the
additional value is allocated; no base stock engine is duplicated or mutated.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN, localcontext


ZERO = Decimal(0)
CENT = Decimal("0.01")


class PurchaseLandedCostFlowError(Exception):
    pass


@dataclass(frozen=True)
class CostDestination:
    key: str
    kind: str
    product_id: int
    warehouse_id: int
    event_date: date
    stock_lot_id: int | None = None
    inventory_cost_entry_id: int | None = None


@dataclass(frozen=True)
class CostFlowEdge:
    fraction: Decimal
    target_node: str | None = None
    destination: CostDestination | None = None


def allocate_landed_cost_flow(
    *, amount: Decimal, root: str, graph: dict[str, tuple[CostFlowEdge, ...]],
) -> tuple[tuple[CostDestination, Decimal], ...]:
    """Conserve every cent, including amounts smaller than the destination count.

    The graph is supplied from existing FIFO/MA physical history. Round only
    final destinations with a deterministic largest-remainder allocation.
    """
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(CENT):
        raise PurchaseLandedCostFlowError("Cost must be positive whole cents")
    totals = defaultdict(lambda: ZERO)
    destinations = {}

    def visit(node, weight, ancestors):
        if node in ancestors:
            raise PurchaseLandedCostFlowError("Cyclic receipt provenance")
        edges = graph.get(node)
        if not edges:
            raise PurchaseLandedCostFlowError(f"Missing cost-flow node: {node}")
        if any(not e.fraction.is_finite() or e.fraction < 0 for e in edges):
            raise PurchaseLandedCostFlowError("Invalid cost-flow fraction")
        if abs(sum((e.fraction for e in edges), ZERO) - 1) > Decimal("1e-24"):
            raise PurchaseLandedCostFlowError("Cost-flow quantities do not conserve value")
        for edge in edges:
            if (edge.target_node is None) == (edge.destination is None):
                raise PurchaseLandedCostFlowError("Edge must have exactly one destination")
            if not edge.fraction:
                continue
            value = weight * edge.fraction
            if edge.target_node is not None:
                visit(edge.target_node, value, ancestors | {node})
            else:
                dest = edge.destination
                if dest.key in destinations and destinations[dest.key] != dest:
                    raise PurchaseLandedCostFlowError("Conflicting destination identity")
                destinations[dest.key] = dest
                totals[dest.key] += value

    with localcontext() as ctx:
        ctx.prec = 50
        visit(root, Decimal(1), frozenset())
        if not totals:
            raise PurchaseLandedCostFlowError("No value destinations")
        total = sum(totals.values(), ZERO)
        exact = {k: amount * w / total for k, w in totals.items()}
        rounded = {k: v.quantize(CENT, rounding=ROUND_DOWN) for k, v in exact.items()}
        pennies = int((amount - sum(rounded.values(), ZERO)) / CENT)
        ranked = sorted(exact, key=lambda k: (exact[k] - rounded[k], k), reverse=True)
        for k in ranked[:pennies]:
            rounded[k] += CENT
        result = tuple((destinations[k], rounded[k]) for k in sorted(rounded) if rounded[k])
    if sum((v for _, v in result), ZERO) != amount:
        raise PurchaseLandedCostFlowError("Allocated value differs from source cost")
    return result
