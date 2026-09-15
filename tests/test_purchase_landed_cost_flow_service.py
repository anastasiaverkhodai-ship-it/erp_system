from datetime import date
from decimal import Decimal as D

import pytest

from app.services.purchase_landed_cost_flow_service import (
    CostDestination, CostFlowEdge, PurchaseLandedCostFlowError, allocate_landed_cost_flow,
)


def dest(key):
    return CostDestination(key, "on_hand", 1, 1, date(2026, 9, 1))


def test_routes_transfer_and_return_without_double_counting():
    graph = {
        "receipt": (CostFlowEdge(D("0.4"), destination=dest("source")),
                    CostFlowEdge(D("0.6"), target_node="transfer")),
        "transfer": (CostFlowEdge(D("0.5"), destination=dest("destination")),
                     CostFlowEdge(D("0.25"), destination=dest("sold")),
                     CostFlowEdge(D("0.25"), target_node="returned")),
        "returned": (CostFlowEdge(D("1"), destination=dest("return-stock")),),
    }
    result = allocate_landed_cost_flow(amount=D("100"), root="receipt", graph=graph)
    assert {d.key: value for d, value in result} == {
        "source": D("40"), "destination": D("30"), "sold": D("15"), "return-stock": D("15"),
    }


def test_one_cent_across_many_destinations_is_conserved():
    graph = {"r": tuple(CostFlowEdge(D("0.25"), destination=dest(str(i))) for i in range(4))}
    result = allocate_landed_cost_flow(amount=D("0.01"), root="r", graph=graph)
    assert len(result) == 1 and result[0][1] == D("0.01")


@pytest.mark.parametrize("graph", [
    {"r": (CostFlowEdge(D("1"), target_node="r"),)},
    {"r": (CostFlowEdge(D("0.5"), destination=dest("lost")),)},
    {"r": (CostFlowEdge(D("NaN"), destination=dest("bad")),)},
    {"r": (CostFlowEdge(D("1"), target_node="missing"),)},
])
def test_invalid_provenance_fails_closed(graph):
    with pytest.raises(PurchaseLandedCostFlowError):
        allocate_landed_cost_flow(amount=D("1"), root="r", graph=graph)
