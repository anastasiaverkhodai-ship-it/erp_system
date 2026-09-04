from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.sales_return_recognition_persistence_service as service

from app.services.sales_return_recognition_persistence_service import (
    SalesReturnRecognitionDataIntegrityError,
    SalesReturnRecognitionLockedSources,
    build_current_sales_return_recognition_targets,
    build_sales_return_recognition_source_plan,
    reconcile_sales_return_recognition_source,
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
    return_id=10,
    sales_id=20,
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


def event(
    *,
    event_id,
    return_id=10,
    sales_id=20,
    recognition_date=D2,
    quantity="1",
    gross="60.00",
    tax="10.00",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        trade_return_event_id=return_id,
        sales_recognition_event_id=sales_id,
        recognition_date=recognition_date,
        returned_quantity=Decimal(
            quantity
        ),
        returned_gross_amount=Decimal(
            gross
        ),
        returned_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def locked_sources(
    *,
    trade_active=True,
    sales_active=True,
):
    return SalesReturnRecognitionLockedSources(
        trade_return_event=SimpleNamespace(
            id=10,
            company_id=1,
            direction="sale",
            original_fulfillment_id=100,
            original_fulfillment_line_id=101,
            product_id=5,
            return_date=D2,
            returned_quantity=Decimal(
                "2"
            ),
            reversal_of_id=None,
        ),
        sales_recognition_event=SimpleNamespace(
            id=20,
            company_id=1,
            invoice_fulfillment_allocation_id=30,
            recognition_date=D1,
            recognized_quantity=Decimal(
                "2"
            ),
            recognized_gross_amount=Decimal(
                "120.00"
            ),
            recognized_tax_amount=Decimal(
                "20.00"
            ),
            currency_code="UAH",
            reversal_of_id=None,
        ),
        invoice_fulfillment_allocation=SimpleNamespace(
            id=30,
            company_id=1,
            fulfillment_id=100,
            fulfillment_line_id=101,
            product_id=5,
            status="active",
        ),
        trade_return_active=trade_active,
        sales_recognition_active=sales_active,
    )


class FakeDb:
    def __init__(
        self,
    ):
        self.added = []
        self.flush = AsyncMock()

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )


def test_build_current_empty():
    assert (
        build_current_sales_return_recognition_targets(
            events=(),
            currency_code="UAH",
        )
        == ()
    )


def test_build_current_active_original():
    result = (
        build_current_sales_return_recognition_targets(
            events=(
                event(
                    event_id=1
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        target(),
    )


def test_reversal_removes_original_from_current():
    result = (
        build_current_sales_return_recognition_targets(
            events=(
                event(
                    event_id=1
                ),
                event(
                    event_id=2,
                    reversal_of_id=1,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == ()


def test_replacement_becomes_current():
    result = (
        build_current_sales_return_recognition_targets(
            events=(
                event(
                    event_id=1,
                    gross="60.00",
                ),
                event(
                    event_id=2,
                    gross="60.00",
                    reversal_of_id=1,
                ),
                event(
                    event_id=3,
                    gross="60.01",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].gross_amount
        == Decimal(
            "60.01"
        )
    )


def test_duplicate_active_pair_is_corruption():
    with pytest.raises(
        SalesReturnRecognitionDataIntegrityError
    ):
        build_current_sales_return_recognition_targets(
            events=(
                event(
                    event_id=1
                ),
                event(
                    event_id=2
                ),
            ),
            currency_code="UAH",
        )


def test_plan_new_positive_creates_original():
    plan = (
        build_sales_return_recognition_source_plan(
            events=(),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target()


def test_plan_exact_is_noop():
    plan = (
        build_sales_return_recognition_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_plan_change_reverses_and_replaces():
    changed = target(
        gross="60.01"
    )

    plan = (
        build_sales_return_recognition_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            target=changed,
            currency_code="UAH",
        )
    )

    assert (
        plan.reversal_event_ids
        == (
            1,
        )
    )

    assert (
        plan.replacement_target
        == changed
    )


def test_plan_zero_reverses_only():
    zero = target(
        quantity="0",
        gross="0",
        tax="0",
    )

    plan = (
        build_sales_return_recognition_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            target=zero,
            currency_code="UAH",
        )
    )

    assert (
        plan.reversal_event_ids
        == (
            1,
        )
    )

    assert (
        plan.replacement_target
        is None
    )


def test_pair_date_change_is_rejected():
    with pytest.raises(
        SalesReturnRecognitionDataIntegrityError
    ):
        build_sales_return_recognition_source_plan(
            events=(
                event(
                    event_id=1
                ),
            ),
            target=target(
                event_date=D3
            ),
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_executor_new_original(
    monkeypatch,
):
    db = FakeDb()

    monkeypatch.setattr(
        service,
        "_lock_sales_return_recognition_sources",
        AsyncMock(
            return_value=locked_sources()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_pair_history",
        AsyncMock(
            return_value=()
        ),
    )

    created = (
        await reconcile_sales_return_recognition_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=7,
        )
    )

    assert len(
        created
    ) == 1

    assert len(
        db.added
    ) == 1

    assert (
        created[0]
        .trade_return_event_id
        == 10
    )

    assert (
        created[0]
        .sales_recognition_event_id
        == 20
    )

    assert (
        created[0]
        .returned_gross_amount
        == Decimal(
            "60.00"
        )
    )

    assert (
        created[0].reversal_of_id
        is None
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_exact_noop(
    monkeypatch,
):
    db = FakeDb()

    monkeypatch.setattr(
        service,
        "_lock_sales_return_recognition_sources",
        AsyncMock(
            return_value=locked_sources()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_pair_history",
        AsyncMock(
            return_value=(
                event(
                    event_id=1
                ),
            )
        ),
    )

    created = (
        await reconcile_sales_return_recognition_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=7,
        )
    )

    assert created == ()
    assert db.added == []
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_change_creates_reversal_and_replacement(
    monkeypatch,
):
    db = FakeDb()

    monkeypatch.setattr(
        service,
        "_lock_sales_return_recognition_sources",
        AsyncMock(
            return_value=locked_sources()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_pair_history",
        AsyncMock(
            return_value=(
                event(
                    event_id=1
                ),
            )
        ),
    )

    created = (
        await reconcile_sales_return_recognition_source(
            db,
            company_id=1,
            target=target(
                gross="60.01"
            ),
            currency_code="UAH",
            created_by=7,
            reversal_date=D3,
        )
    )

    assert len(
        created
    ) == 2

    reversal = created[0]
    replacement = created[1]

    assert (
        reversal.reversal_of_id
        == 1
    )

    assert (
        reversal.recognition_date
        == D3
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.returned_gross_amount
        == Decimal(
            "60.01"
        )
    )


@pytest.mark.asyncio
async def test_positive_target_requires_active_sources(
    monkeypatch,
):
    db = FakeDb()

    monkeypatch.setattr(
        service,
        "_lock_sales_return_recognition_sources",
        AsyncMock(
            return_value=locked_sources(
                trade_active=False
            )
        ),
    )

    with pytest.raises(
        service.SalesReturnRecognitionInactiveSourceError
    ):
        await reconcile_sales_return_recognition_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=7,
        )


@pytest.mark.asyncio
async def test_zero_target_can_clean_inactive_source(
    monkeypatch,
):
    db = FakeDb()

    monkeypatch.setattr(
        service,
        "_lock_sales_return_recognition_sources",
        AsyncMock(
            return_value=locked_sources(
                trade_active=False,
                sales_active=False,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_pair_history",
        AsyncMock(
            return_value=(
                event(
                    event_id=1
                ),
            )
        ),
    )

    zero = target(
        quantity="0",
        gross="0",
        tax="0",
    )

    created = (
        await reconcile_sales_return_recognition_source(
            db,
            company_id=1,
            target=zero,
            currency_code="UAH",
            created_by=7,
        )
    )

    assert len(
        created
    ) == 1

    assert (
        created[0].reversal_of_id
        == 1
    )
