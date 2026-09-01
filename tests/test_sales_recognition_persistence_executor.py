from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.sales_recognition_persistence_service as service
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionTarget,
)


D1 = date(2026, 9, 1)

D2 = date(2026, 9, 2)


class FakeDB:
    def __init__(
        self,
    ):
        self.added = []
        self.flush_calls = 0

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

    async def flush(
        self,
    ):
        self.flush_calls += 1


def _target(
    *,
    source_id=10,
    quantity="1",
    gross="33.33",
    tax="6.67",
    event_date=D1,
):
    return SalesRecognitionTarget(
        source_id=source_id,
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
    )


def _event(
    *,
    event_id=1,
    source_id=10,
    quantity="1",
    gross="33.33",
    tax="6.67",
    event_date=D1,
    reversal_of_id=None,
):
    return SalesRecognitionEvent(
        id=event_id,
        company_id=1,
        invoice_fulfillment_allocation_id=(
            source_id
        ),
        recognition_date=event_date,
        recognized_quantity=Decimal(
            quantity
        ),
        recognized_gross_amount=Decimal(
            gross
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


async def _install_source(
    monkeypatch,
    *,
    events,
):
    async def fake_lock(
        db,
        *,
        company_id,
        source_id,
    ):
        return SimpleNamespace(
            id=source_id,
            company_id=company_id,
        )

    async def fake_load(
        db,
        *,
        company_id,
        source_id,
        lock_rows,
    ):
        return tuple(
            events
        )

    monkeypatch.setattr(
        service,
        "_lock_sales_recognition_source",
        fake_lock,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_recognition_events",
        fake_load,
    )


@pytest.mark.asyncio
async def test_executor_new_source_creates_full_original(
    monkeypatch,
):
    await _install_source(
        monkeypatch,
        events=(),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_sales_recognition_source(
            db,
            company_id=1,
            target=_target(),
            currency_code="UAH",
            created_by=2,
        )
    )

    assert len(created) == 1

    event = created[0]

    assert isinstance(
        event,
        SalesRecognitionEvent,
    )

    assert (
        event
        .invoice_fulfillment_allocation_id
        == 10
    )

    assert (
        event.recognized_quantity
        == Decimal("1")
    )

    assert (
        event.recognized_gross_amount
        == Decimal("33.33")
    )

    assert (
        event.recognized_tax_amount
        == Decimal("6.67")
    )

    assert (
        event.reversal_of_id
        is None
    )

    assert event.created_by == 2
    assert db.added == [
        event
    ]
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_executor_exact_target_is_noop(
    monkeypatch,
):
    await _install_source(
        monkeypatch,
        events=(
            _event(),
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_sales_recognition_source(
            db,
            company_id=1,
            target=_target(),
            currency_code="UAH",
            created_by=2,
        )
    )

    assert created == ()
    assert db.added == []
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_executor_penny_change_reverses_and_replaces(
    monkeypatch,
):
    original = _event()

    await _install_source(
        monkeypatch,
        events=(
            original,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_sales_recognition_source(
            db,
            company_id=1,
            target=_target(
                gross="33.34",
                tax="6.66",
            ),
            currency_code="UAH",
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(created) == 2

    reversal = created[0]
    replacement = created[1]

    assert reversal.reversal_of_id == 1
    assert reversal.recognition_date == D2

    assert (
        reversal.recognized_quantity
        == Decimal("1")
    )

    assert (
        reversal.recognized_gross_amount
        == Decimal("33.33")
    )

    assert (
        reversal.recognized_tax_amount
        == Decimal("6.67")
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.recognized_quantity
        == Decimal("1")
    )

    assert (
        replacement.recognized_gross_amount
        == Decimal("33.34")
    )

    assert (
        replacement.recognized_tax_amount
        == Decimal("6.66")
    )

    assert (
        replacement.recognition_date
        == D1
    )

    assert db.added == [
        reversal,
        replacement,
    ]

    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_executor_zero_target_reverses_only(
    monkeypatch,
):
    original = _event()

    await _install_source(
        monkeypatch,
        events=(
            original,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_sales_recognition_source(
            db,
            company_id=1,
            target=_target(
                quantity="0",
                gross="0",
                tax="0",
            ),
            currency_code="UAH",
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(created) == 1

    reversal = created[0]

    assert reversal.reversal_of_id == 1
    assert reversal.recognition_date == D2
    assert db.added == [
        reversal
    ]
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_executor_reversed_history_allows_clean_original(
    monkeypatch,
):
    original = _event(
        event_id=1,
    )

    reversal = _event(
        event_id=2,
        reversal_of_id=1,
    )

    await _install_source(
        monkeypatch,
        events=(
            original,
            reversal,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_sales_recognition_source(
            db,
            company_id=1,
            target=_target(
                gross="33.34",
                tax="6.66",
            ),
            currency_code="UAH",
            created_by=2,
        )
    )

    assert len(created) == 1

    replacement = created[0]

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.recognized_quantity
        == Decimal("1")
    )

    assert (
        replacement.recognized_gross_amount
        == Decimal("33.34")
    )


@pytest.mark.asyncio
async def test_executor_validates_company_before_db(
    monkeypatch,
):
    db = FakeDB()

    with pytest.raises(
        ValueError,
        match="company_id",
    ):
        await (
            service
            .reconcile_sales_recognition_source(
                db,
                company_id=0,
                target=_target(),
                currency_code="UAH",
                created_by=1,
            )
        )

    assert db.added == []
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_executor_validates_created_by_before_db(
    monkeypatch,
):
    db = FakeDB()

    with pytest.raises(
        ValueError,
        match="created_by",
    ):
        await (
            service
            .reconcile_sales_recognition_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=0,
            )
        )

    assert db.added == []
    assert db.flush_calls == 0
