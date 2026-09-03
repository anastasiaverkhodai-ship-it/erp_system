from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.input_vat_fulfillment_bridge_reconciliation_service as service
from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeDataIntegrityError,
    InputVatFulfillmentBridgeTarget,
)
from app.services.input_vat_fulfillment_bridge_reconciliation_service import (
    build_input_vat_fulfillment_bridge_reconciliation_targets,
    reconcile_input_vat_fulfillment_bridge_for_tax_calculation,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)

D3 = date(
    2026,
    9,
    3,
)


def target(
    *,
    source_id: int,
    amount: str,
    event_date: date = D1,
    tax_calculation_id: int = 20,
):
    return InputVatFulfillmentBridgeTarget(
        tax_calculation_id=(
            tax_calculation_id
        ),
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code="UAH",
    )


def calculation():
    return SimpleNamespace(
        id=20,
        company_id=1,
        trade_document_id=100,
        trade_document_line_id=1000,
        product_id=7,
        tax_type=TaxType.VAT,
        direction=TaxDirection.INPUT,
        tax_amount=Decimal(
            "0.02"
        ),
        currency_code="UAH",
    )


def test_exact_targets_produce_no_actions():
    actions = (
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                ),
            ),
            current_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                ),
            ),
        )
    )

    assert actions == ()


def test_stale_persistent_source_is_zeroed():
    actions = (
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(),
            current_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                    event_date=D1,
                ),
            ),
        )
    )

    assert len(
        actions
    ) == 1

    assert (
        actions[0].source_id
        == 1
    )

    assert (
        actions[0].amount
        == Decimal("0")
    )

    assert (
        actions[0].event_date
        == D1
    )


def test_decrease_runs_before_increase():
    actions = (
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="0.00",
                    event_date=D1,
                ),
                target(
                    source_id=2,
                    amount="0.01",
                    event_date=D2,
                ),
            ),
            current_targets=(
                target(
                    source_id=1,
                    amount="0.01",
                    event_date=D1,
                ),
            ),
        )
    )

    assert [
        (
            action.source_id,
            action.amount,
        )
        for action in actions
    ] == [
        (
            1,
            Decimal("0.00"),
        ),
        (
            2,
            Decimal("0.01"),
        ),
    ]


def test_new_zero_target_is_omitted():
    actions = (
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="0.00",
                ),
            ),
            current_targets=(),
        )
    )

    assert actions == ()


def test_existing_source_event_date_cannot_change():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="event_date changed",
    ):
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                    event_date=D2,
                ),
            ),
            current_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                    event_date=D1,
                ),
            ),
        )


def test_existing_source_tax_calculation_cannot_change():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="tax_calculation_id changed",
    ):
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                    tax_calculation_id=21,
                ),
            ),
            current_targets=(
                target(
                    source_id=1,
                    amount="10.00",
                    tax_calculation_id=20,
                ),
            ),
        )


def test_duplicate_desired_source_fails_closed():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="Duplicate desired",
    ):
        build_input_vat_fulfillment_bridge_reconciliation_targets(
            desired_targets=(
                target(
                    source_id=1,
                    amount="1.00",
                ),
                target(
                    source_id=1,
                    amount="2.00",
                ),
            ),
            current_targets=(),
        )


@pytest.mark.asyncio
async def test_reconciliation_executes_decrease_before_increase(
    monkeypatch,
):
    calc = calculation()

    monkeypatch.setattr(
        service,
        "_load_input_tax_calculation",
        AsyncMock(
            return_value=calc
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_quantity",
        AsyncMock(
            return_value=Decimal(
                "3"
            )
        ),
    )

    candidates = (
        service
        .InputVatFulfillmentBridgeCandidate(
            source_id=1,
            event_date=D1,
            quantity=Decimal(
                "1"
            ),
        ),
        service
        .InputVatFulfillmentBridgeCandidate(
            source_id=2,
            event_date=D2,
            quantity=Decimal(
                "1"
            ),
        ),
        service
        .InputVatFulfillmentBridgeCandidate(
            source_id=3,
            event_date=D3,
            quantity=Decimal(
                "1"
            ),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_fulfillment_candidates",
        AsyncMock(
            return_value=candidates
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_bridge_history",
        AsyncMock(
            return_value=()
        ),
    )

    current_targets = (
        target(
            source_id=1,
            amount="0.01",
            event_date=D1,
        ),
        target(
            source_id=2,
            amount="0.01",
            event_date=D2,
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "build_current_input_vat_"
            "fulfillment_bridge_targets"
        ),
        lambda **kwargs: current_targets,
    )

    calls = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        currency_code,
        created_by,
        reversal_date,
    ):
        calls.append(
            (
                target.source_id,
                target.amount,
                reversal_date,
            )
        )

        return (
            SimpleNamespace(
                id=(
                    100
                    + len(
                        calls
                    )
                )
            ),
        )

    monkeypatch.setattr(
        service,
        (
            "reconcile_input_vat_"
            "fulfillment_bridge_source"
        ),
        reconcile,
    )

    result = (
        await reconcile_input_vat_fulfillment_bridge_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D3,
            created_by=1,
        )
    )

    # Cumulative target for VAT 0.02 over 3 equal allocations:
    #
    #   source 1 = 0.01
    #   source 2 = 0.00
    #   source 3 = 0.01
    #
    # Current source 2 is 0.01, therefore it must be removed before
    # source 3 is increased from absent -> 0.01.
    assert calls == [
        (
            2,
            Decimal("0.00"),
            D3,
        ),
        (
            3,
            Decimal("0.01"),
            D3,
        ),
    ]

    assert (
        result.created_event_ids
        == (
            101,
            102,
        )
    )


@pytest.mark.asyncio
async def test_stale_source_is_reconciled_to_zero(
    monkeypatch,
):
    calc = calculation()

    monkeypatch.setattr(
        service,
        "_load_input_tax_calculation",
        AsyncMock(
            return_value=calc
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_quantity",
        AsyncMock(
            return_value=Decimal(
                "3"
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_fulfillment_candidates",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_bridge_history",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "build_current_input_vat_"
            "fulfillment_bridge_targets"
        ),
        lambda **kwargs: (
            target(
                source_id=9,
                amount="0.02",
                event_date=D1,
            ),
        ),
    )

    seen = []

    async def reconcile(
        db,
        *,
        company_id,
        target,
        currency_code,
        created_by,
        reversal_date,
    ):
        seen.append(
            target
        )

        return (
            SimpleNamespace(
                id=501
            ),
        )

    monkeypatch.setattr(
        service,
        (
            "reconcile_input_vat_"
            "fulfillment_bridge_source"
        ),
        reconcile,
    )

    result = (
        await reconcile_input_vat_fulfillment_bridge_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D3,
            created_by=1,
        )
    )

    assert len(
        seen
    ) == 1

    assert (
        seen[0].source_id
        == 9
    )

    assert (
        seen[0].amount
        == Decimal("0")
    )

    assert (
        result.created_event_ids
        == (
            501,
        )
    )


@pytest.mark.parametrize(
    (
        "company_id",
        "tax_calculation_id",
        "created_by",
    ),
    [
        (
            0,
            1,
            1,
        ),
        (
            1,
            0,
            1,
        ),
        (
            1,
            1,
            0,
        ),
    ],
)
@pytest.mark.asyncio
async def test_reconciliation_validates_context(
    company_id,
    tax_calculation_id,
    created_by,
):
    with pytest.raises(
        ValueError,
    ):
        await reconcile_input_vat_fulfillment_bridge_for_tax_calculation(
            object(),
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            adjustment_date=D1,
            created_by=created_by,
        )
