from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.vat_advance_bridge_persistence_service as service

from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)
from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
    VatAdvanceBridgeTarget,
)


D1 = date(
    2026,
    9,
    2,
)

D2 = date(
    2026,
    9,
    3,
)


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
    tax_calculation_id=20,
    amount="6.67",
    event_date=D1,
):
    return VatAdvanceBridgeTarget(
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


def _event(
    *,
    event_id=1,
    source_id=10,
    tax_calculation_id=20,
    amount="6.67",
    event_date=D1,
    reversal_of_id=None,
):
    return VatAdvanceBridgeEvent(
        id=event_id,
        company_id=1,
        tax_calculation_id=(
            tax_calculation_id
        ),
        invoice_fulfillment_allocation_id=(
            source_id
        ),
        bridge_date=event_date,
        bridged_tax_amount=Decimal(
            amount
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


async def _install(
    monkeypatch,
    *,
    events=(),
    source_overrides=None,
    calculation_overrides=None,
):
    source_data = dict(
        id=10,
        company_id=1,
        invoice_id=100,
        invoice_line_id=101,
        product_id=500,
    )

    if source_overrides:
        source_data.update(
            source_overrides
        )

    calculation_data = dict(
        id=20,
        company_id=1,
        trade_document_id=100,
        trade_document_line_id=101,
        product_id=500,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        currency_code="UAH",
    )

    if calculation_overrides:
        calculation_data.update(
            calculation_overrides
        )

    async def fake_lock_source(
        db,
        *,
        company_id,
        source_id,
    ):
        return SimpleNamespace(
            **source_data
        )

    async def fake_lock_calculation(
        db,
        *,
        company_id,
        tax_calculation_id,
    ):
        return SimpleNamespace(
            **calculation_data
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
        "_lock_vat_advance_bridge_source",
        fake_lock_source,
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_vat_advance_bridge_"
            "tax_calculation"
        ),
        fake_lock_calculation,
    )

    monkeypatch.setattr(
        service,
        "_load_vat_advance_bridge_events",
        fake_load,
    )


@pytest.mark.asyncio
async def test_executor_new_source_creates_original(
    monkeypatch,
):
    await _install(
        monkeypatch,
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_vat_advance_bridge_source(
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
        VatAdvanceBridgeEvent,
    )

    assert event.company_id == 1
    assert event.tax_calculation_id == 20

    assert (
        event.invoice_fulfillment_allocation_id
        == 10
    )

    assert event.bridge_date == D1

    assert (
        event.bridged_tax_amount
        == Decimal("6.67")
    )

    assert event.currency_code == "UAH"
    assert event.created_by == 2
    assert event.reversal_of_id is None

    assert db.added == [
        event
    ]

    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_executor_exact_target_is_noop(
    monkeypatch,
):
    await _install(
        monkeypatch,
        events=(
            _event(),
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_vat_advance_bridge_source(
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
async def test_executor_change_reverses_and_replaces(
    monkeypatch,
):
    original = _event()

    await _install(
        monkeypatch,
        events=(
            original,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_vat_advance_bridge_source(
            db,
            company_id=1,
            target=_target(
                amount="10.00",
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
    assert reversal.bridge_date == D2

    assert (
        reversal.bridged_tax_amount
        == Decimal("6.67")
    )

    assert replacement.reversal_of_id is None
    assert replacement.bridge_date == D1

    assert (
        replacement.bridged_tax_amount
        == Decimal("10.00")
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

    await _install(
        monkeypatch,
        events=(
            original,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_vat_advance_bridge_source(
            db,
            company_id=1,
            target=_target(
                amount="0.00",
            ),
            currency_code="UAH",
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(created) == 1

    reversal = created[0]

    assert reversal.reversal_of_id == 1
    assert reversal.bridge_date == D2

    assert (
        reversal.bridged_tax_amount
        == Decimal("6.67")
    )

    assert db.added == [
        reversal
    ]

    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_executor_reversed_history_allows_new_original(
    monkeypatch,
):
    original = _event(
        event_id=1,
    )

    reversal = _event(
        event_id=2,
        event_date=D2,
        reversal_of_id=1,
    )

    await _install(
        monkeypatch,
        events=(
            original,
            reversal,
        ),
    )

    db = FakeDB()

    created = (
        await service
        .reconcile_vat_advance_bridge_source(
            db,
            company_id=1,
            target=_target(
                amount="10.00",
            ),
            currency_code="UAH",
            created_by=2,
        )
    )

    assert len(created) == 1

    replacement = created[0]

    assert replacement.reversal_of_id is None

    assert (
        replacement.bridged_tax_amount
        == Decimal("10.00")
    )


@pytest.mark.asyncio
async def test_executor_rejects_wrong_invoice(
    monkeypatch,
):
    await _install(
        monkeypatch,
        calculation_overrides={
            "trade_document_id": 999,
        },
    )

    db = FakeDB()

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="source Invoice",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )

    assert db.added == []
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_executor_rejects_wrong_invoice_line(
    monkeypatch,
):
    await _install(
        monkeypatch,
        calculation_overrides={
            "trade_document_line_id": 999,
        },
    )

    db = FakeDB()

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="source Invoice line",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )


@pytest.mark.asyncio
async def test_executor_rejects_wrong_product(
    monkeypatch,
):
    await _install(
        monkeypatch,
        calculation_overrides={
            "product_id": 999,
        },
    )

    db = FakeDB()

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="product does not match",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )


@pytest.mark.asyncio
async def test_executor_rejects_input_vat(
    monkeypatch,
):
    await _install(
        monkeypatch,
        calculation_overrides={
            "direction": TaxDirection.INPUT,
        },
    )

    db = FakeDB()

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="OUTPUT VAT",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )


@pytest.mark.asyncio
async def test_executor_rejects_currency_mismatch(
    monkeypatch,
):
    await _install(
        monkeypatch,
        calculation_overrides={
            "currency_code": "EUR",
        },
    )

    db = FakeDB()

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="TaxCalculation currency",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )


@pytest.mark.asyncio
async def test_executor_validates_ids_before_db(
    monkeypatch,
):
    await _install(
        monkeypatch,
    )

    db = FakeDB()

    with pytest.raises(
        ValueError,
        match="company_id",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=0,
                target=_target(),
                currency_code="UAH",
                created_by=2,
            )
        )

    with pytest.raises(
        ValueError,
        match="created_by",
    ):
        await (
            service
            .reconcile_vat_advance_bridge_source(
                db,
                company_id=1,
                target=_target(),
                currency_code="UAH",
                created_by=0,
            )
        )

    assert db.added == []
    assert db.flush_calls == 0
