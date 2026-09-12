from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
)
from app.services.purchase_value_correction_moving_average_replay_reconciliation_service import (
    _build_composed_desired_targets,
)
from app.services.purchase_value_correction_moving_average_peer_reconciliation_service import (
    _target_from_composed_peer_impact,
)


D1 = date(
    2026,
    1,
    1,
)

D3 = date(
    2026,
    1,
    3,
)


def composed_impact(
    *,
    company_id=1,
    product_id=7,
    warehouse_id=20,
    effect_kind="on_hand",
    recognition_date=D1,
    movement_id=None,
    ice_id=None,
    original="20",
    corrected="18",
):
    return SimpleNamespace(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        effect_kind=effect_kind,
        recognition_date=recognition_date,
        quantity=Decimal("10"),
        original_valuation_amount=Decimal(
            original
        ),
        corrected_valuation_amount=Decimal(
            corrected
        ),
        source_moving_average_movement_id=(
            movement_id
        ),
        source_inventory_cost_entry_id=(
            ice_id
        ),
        warehouse_transfer_valuation_layer_ids=(
            501,
        ),
    )


def single_source():
    return SimpleNamespace(
        company_id=1,
        product_id=7,
        warehouse_id=10,
        purchase_value_correction_allocation_event_id=100,
        currency_code="UAH",
    )


def peer_source():
    return SimpleNamespace(
        company_id=1,
        product_id=7,
        warehouse_id=10,
        base_source=SimpleNamespace(
            currency_code="UAH",
        ),
    )


def test_single_target_uses_final_destination_warehouse():
    composition = SimpleNamespace(
        impacts=(
            composed_impact(
                warehouse_id=20,
            ),
        ),
    )

    targets = (
        _build_composed_desired_targets(
            source=single_source(),
            composition=composition,
        )
    )

    assert len(targets) == 1

    target = targets[0]

    assert target.warehouse_id == 20
    assert (
        target.purchase_value_correction_allocation_event_id
        == 100
    )

    assert target.recognition_date == D1


def test_single_target_uses_downstream_destination_issue():
    composition = SimpleNamespace(
        impacts=(
            composed_impact(
                warehouse_id=20,
                effect_kind="issued",
                recognition_date=D3,
                movement_id=301,
                ice_id=401,
            ),
        ),
    )

    target = (
        _build_composed_desired_targets(
            source=single_source(),
            composition=composition,
        )[0]
    )

    assert target.warehouse_id == 20
    assert target.effect_kind == "issued"
    assert target.recognition_date == D3
    assert (
        target.source_moving_average_movement_id
        == 301
    )
    assert (
        target.source_inventory_cost_entry_id
        == 401
    )


def test_single_allows_same_on_hand_shape_in_two_warehouses():
    composition = SimpleNamespace(
        impacts=(
            composed_impact(
                warehouse_id=10,
                original="10",
                corrected="9",
            ),
            composed_impact(
                warehouse_id=20,
                original="10",
                corrected="9",
            ),
        ),
    )

    targets = (
        _build_composed_desired_targets(
            source=single_source(),
            composition=composition,
        )
    )

    assert {
        target.warehouse_id
        for target in targets
    } == {
        10,
        20,
    }


def test_single_rejects_wrong_company():
    composition = SimpleNamespace(
        impacts=(
            composed_impact(
                company_id=999,
            ),
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="company",
    ):
        _build_composed_desired_targets(
            source=single_source(),
            composition=composition,
        )


def test_single_rejects_wrong_product():
    composition = SimpleNamespace(
        impacts=(
            composed_impact(
                product_id=999,
            ),
        ),
    )

    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="product",
    ):
        _build_composed_desired_targets(
            source=single_source(),
            composition=composition,
        )


def test_peer_target_keeps_peer_allocation_but_routes_warehouse():
    target = (
        _target_from_composed_peer_impact(
            source=peer_source(),
            allocation_event_id=777,
            impact=composed_impact(
                warehouse_id=20,
                effect_kind="issued",
                recognition_date=D3,
                movement_id=301,
                ice_id=401,
            ),
        )
    )

    assert (
        target.purchase_value_correction_allocation_event_id
        == 777
    )

    assert target.warehouse_id == 20
    assert target.recognition_date == D3
    assert (
        target.source_moving_average_movement_id
        == 301
    )
    assert (
        target.source_inventory_cost_entry_id
        == 401
    )

    assert target.currency_code == "UAH"


def test_peer_target_rejects_wrong_company():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="company",
    ):
        _target_from_composed_peer_impact(
            source=peer_source(),
            allocation_event_id=777,
            impact=composed_impact(
                company_id=999,
            ),
        )


def test_peer_target_rejects_wrong_product():
    with pytest.raises(
        PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
        match="product",
    ):
        _target_from_composed_peer_impact(
            source=peer_source(),
            allocation_event_id=777,
            impact=composed_impact(
                product_id=999,
            ),
        )
