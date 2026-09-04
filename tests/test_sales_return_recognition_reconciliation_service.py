from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_recognition_reconciliation_service as service

from app.services.sales_return_recognition_reconciliation_service import (
    SalesReturnRecognitionCapacitySource,
    SalesReturnRecognitionReconciliationDataIntegrityError,
    build_sales_return_recognition_reconciliation_targets,
    reconcile_sales_return_recognition_for_fulfillment_line,
)
from app.services.trade_return_calculation_service import (
    TradeReturnTarget,
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
    return_id,
    sales_id,
    event_date=D2,
    quantity="1",
    gross="60.00",
    tax="10.00",
):
    return TradeReturnTarget(
        return_source_id=return_id,
        economic_source_id=sales_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
        gross_amount=Decimal(
            gross
        ),
        tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
    )


def test_exact_pairs_are_omitted():
    item = target(
        return_id=10,
        sales_id=20,
    )

    assert (
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(
                item,
            ),
            current_targets=(
                item,
            ),
        )
        == ()
    )


def test_removed_pair_becomes_zero_target():
    current = target(
        return_id=10,
        sales_id=20,
    )

    result = (
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(),
            current_targets=(
                current,
            ),
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].return_source_id
        == 10
    )

    assert (
        result[0].economic_source_id
        == 20
    )

    assert (
        result[0].quantity
        == Decimal(
            "0"
        )
    )

    assert (
        result[0].gross_amount
        == Decimal(
            "0"
        )
    )


def test_decrease_is_ordered_before_new_pair():
    current = target(
        return_id=10,
        sales_id=20,
        gross="60.00",
    )

    decreased = target(
        return_id=10,
        sales_id=20,
        gross="50.00",
    )

    new = target(
        return_id=11,
        sales_id=21,
        event_date=D3,
        gross="10.00",
    )

    result = (
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(
                new,
                decreased,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert result == (
        decreased,
        new,
    )


def test_removed_old_sales_event_before_replacement_event():
    current = target(
        return_id=10,
        sales_id=20,
    )

    replacement = target(
        return_id=10,
        sales_id=30,
    )

    result = (
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(
                replacement,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert (
        result[0].economic_source_id
        == 20
    )

    assert (
        result[0].gross_amount
        == Decimal(
            "0"
        )
    )

    assert (
        result[1]
        == replacement
    )


def test_pair_date_change_is_rejected():
    current = target(
        return_id=10,
        sales_id=20,
        event_date=D2,
    )

    desired = target(
        return_id=10,
        sales_id=20,
        event_date=D3,
    )

    with pytest.raises(
        SalesReturnRecognitionReconciliationDataIntegrityError
    ):
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )


def test_duplicate_desired_pair_is_rejected():
    item = target(
        return_id=10,
        sales_id=20,
    )

    with pytest.raises(
        SalesReturnRecognitionReconciliationDataIntegrityError
    ):
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=(
                item,
                item,
            ),
            current_targets=(),
        )


def test_fifo_calculation_uses_allocation_id_not_event_id():
    candidates = (
        service.TradeReturnCandidate(
            source_id=100,
            event_date=D3,
            quantity=Decimal(
                "1"
            ),
        ),
    )

    sources = (
        SalesReturnRecognitionCapacitySource(
            allocation_id=10,
            sales_recognition_event_id=900,
            event_date=D1,
            quantity=Decimal(
                "1"
            ),
            gross_amount=Decimal(
                "40.00"
            ),
            tax_amount=Decimal(
                "6.67"
            ),
            currency_code="UAH",
        ),
        SalesReturnRecognitionCapacitySource(
            allocation_id=20,
            sales_recognition_event_id=100,
            event_date=D1,
            quantity=Decimal(
                "1"
            ),
            gross_amount=Decimal(
                "60.00"
            ),
            tax_amount=Decimal(
                "10.00"
            ),
            currency_code="UAH",
        ),
    )

    result = (
        service._desired_targets_from_sources(
            candidates=candidates,
            capacity_sources=sources,
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    # Allocation 10 wins FIFO although its replacement
    # SalesRecognitionEvent id is much larger.
    assert (
        result[0].economic_source_id
        == 900
    )

    assert (
        result[0].gross_amount
        == Decimal(
            "40.00"
        )
    )


@pytest.mark.asyncio
async def test_complete_reconciliation_creates_desired_pair(
    monkeypatch,
):
    return_event = SimpleNamespace(
        id=100,
        reversal_of_id=None,
        direction="sale",
        return_date=D2,
        returned_quantity=Decimal(
            "1"
        ),
    )

    capacity = (
        SalesReturnRecognitionCapacitySource(
            allocation_id=30,
            sales_recognition_event_id=200,
            event_date=D1,
            quantity=Decimal(
                "2"
            ),
            gross_amount=Decimal(
                "120.00"
            ),
            tax_amount=Decimal(
                "20.00"
            ),
            currency_code="UAH",
        )
    )

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        AsyncMock(
            return_value=(
                return_event,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_capacity_sources",
        AsyncMock(
            return_value=(
                capacity,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_recognition_history",
        AsyncMock(
            return_value=()
        ),
    )

    created_event = SimpleNamespace(
        id=999
    )

    persist = AsyncMock(
        return_value=(
            created_event,
        )
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_return_recognition_source",
        persist,
    )

    result = (
        await reconcile_sales_return_recognition_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=50,
            fulfillment_line_id=51,
            created_by=7,
            adjustment_date=D3,
        )
    )

    assert (
        result.currency_code
        == "UAH"
    )

    assert len(
        result.desired_targets
    ) == 1

    desired = (
        result.desired_targets[0]
    )

    assert (
        desired.return_source_id
        == 100
    )

    assert (
        desired.economic_source_id
        == 200
    )

    assert (
        desired.quantity
        == Decimal(
            "1"
        )
    )

    assert (
        desired.gross_amount
        == Decimal(
            "60.00"
        )
    )

    assert (
        desired.tax_amount
        == Decimal(
            "10.00"
        )
    )

    persist.assert_awaited_once()

    assert (
        result.created_events
        == (
            created_event,
        )
    )


@pytest.mark.asyncio
async def test_no_sources_and_no_history_is_noop(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_capacity_sources",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_recognition_history",
        AsyncMock(
            return_value=()
        ),
    )

    result = (
        await reconcile_sales_return_recognition_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=50,
            fulfillment_line_id=51,
            created_by=7,
        )
    )

    assert (
        result.currency_code
        is None
    )

    assert (
        result.reconciliation_targets
        == ()
    )


@pytest.mark.asyncio
async def test_active_return_without_economic_capacity_fails(
    monkeypatch,
):
    return_event = SimpleNamespace(
        id=100,
        reversal_of_id=None,
        direction="sale",
        return_date=D2,
        returned_quantity=Decimal(
            "1"
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        AsyncMock(
            return_value=(
                return_event,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_capacity_sources",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_sales_return_recognition_history",
        AsyncMock(
            return_value=()
        ),
    )

    with pytest.raises(
        SalesReturnRecognitionReconciliationDataIntegrityError
    ):
        await reconcile_sales_return_recognition_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=50,
            fulfillment_line_id=51,
            created_by=7,
        )
