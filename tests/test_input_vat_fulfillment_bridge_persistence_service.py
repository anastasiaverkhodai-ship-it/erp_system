from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.input_vat_fulfillment_bridge_persistence_service as service
from app.models.input_vat_fulfillment_bridge_event import (
    InputVatFulfillmentBridgeEvent,
)
from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeDataIntegrityError,
    InputVatFulfillmentBridgeTarget,
)
from app.services.input_vat_fulfillment_bridge_persistence_service import (
    InputVatFulfillmentBridgeSourceStateError,
    build_current_input_vat_fulfillment_bridge_targets,
    build_input_vat_fulfillment_bridge_source_plan,
    reconcile_input_vat_fulfillment_bridge_source,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
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


def target(
    *,
    amount: str = "20.00",
    source_id: int = 10,
    tax_calculation_id: int = 20,
    event_date: date = D1,
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


def event(
    *,
    event_id: int,
    amount: str = "20.00",
    source_id: int = 10,
    tax_calculation_id: int = 20,
    bridge_date: date = D1,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=(
            tax_calculation_id
        ),
        invoice_fulfillment_allocation_id=(
            source_id
        ),
        bridge_date=bridge_date,
        bridged_tax_amount=Decimal(
            amount
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def source(
    *,
    status=(
        InvoiceFulfillmentAllocationStatus
        .ACTIVE
    ),
):
    return SimpleNamespace(
        company_id=1,
        id=10,
        invoice_id=100,
        invoice_line_id=1000,
        product_id=7,
        status=status,
    )


def calculation(
    *,
    direction=TaxDirection.INPUT,
):
    return SimpleNamespace(
        company_id=1,
        id=20,
        trade_document_id=100,
        trade_document_line_id=1000,
        product_id=7,
        tax_type=TaxType.VAT,
        direction=direction,
        currency_code="UAH",
    )


def test_new_positive_target_creates_original_plan():
    plan = (
        build_input_vat_fulfillment_bridge_source_plan(
            events=(),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target()


def test_exact_target_is_noop():
    original = event(
        event_id=1
    )

    plan = (
        build_input_vat_fulfillment_bridge_source_plan(
            events=(
                original,
            ),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_changed_amount_reverses_and_replaces():
    original = event(
        event_id=1,
        amount="6.67",
    )

    desired = target(
        amount="6.66",
    )

    plan = (
        build_input_vat_fulfillment_bridge_source_plan(
            events=(
                original,
            ),
            target=desired,
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
        == desired
    )


def test_zero_target_reverses_without_replacement():
    original = event(
        event_id=1,
    )

    plan = (
        build_input_vat_fulfillment_bridge_source_plan(
            events=(
                original,
            ),
            target=target(
                amount="0.00"
            ),
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


def test_reversed_original_is_not_current():
    original = event(
        event_id=1,
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
        bridge_date=D2,
    )

    current = (
        build_current_input_vat_fulfillment_bridge_targets(
            events=(
                original,
                reversal,
            ),
            currency_code="UAH",
        )
    )

    assert current == ()


def test_replacement_becomes_current():
    original = event(
        event_id=1,
        amount="6.67",
    )

    reversal = event(
        event_id=2,
        amount="6.67",
        reversal_of_id=1,
        bridge_date=D2,
    )

    replacement = event(
        event_id=3,
        amount="6.66",
    )

    current = (
        build_current_input_vat_fulfillment_bridge_targets(
            events=(
                original,
                reversal,
                replacement,
            ),
            currency_code="UAH",
        )
    )

    assert len(
        current
    ) == 1

    assert (
        current[0].amount
        == Decimal("6.66")
    )


def test_historical_tax_calculation_cannot_change():
    original = event(
        event_id=1,
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
        bridge_date=D2,
    )

    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="historical tax_calculation_id",
    ):
        build_input_vat_fulfillment_bridge_source_plan(
            events=(
                original,
                reversal,
            ),
            target=target(
                tax_calculation_id=21,
            ),
            currency_code="UAH",
        )


def test_historical_event_date_cannot_change():
    original = event(
        event_id=1,
    )

    reversal = event(
        event_id=2,
        reversal_of_id=1,
        bridge_date=D2,
    )

    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="historical event_date",
    ):
        build_input_vat_fulfillment_bridge_source_plan(
            events=(
                original,
                reversal,
            ),
            target=target(
                event_date=D2,
            ),
            currency_code="UAH",
        )


def test_duplicate_active_originals_fail_closed():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="more than one active",
    ):
        build_current_input_vat_fulfillment_bridge_targets(
            events=(
                event(
                    event_id=1,
                ),
                event(
                    event_id=2,
                    amount="10.00",
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_executor_new_positive_original(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_load_input_vat_"
            "fulfillment_bridge_events"
        ),
        AsyncMock(
            return_value=()
        ),
    )

    created = (
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )
    )

    assert len(
        created
    ) == 1

    original = created[0]

    assert isinstance(
        original,
        InputVatFulfillmentBridgeEvent,
    )

    assert (
        original.reversal_of_id
        is None
    )

    assert (
        original.bridged_tax_amount
        == Decimal("20.00")
    )

    db.add.assert_called_once_with(
        original
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_changed_amount_creates_reversal_and_replacement(
    monkeypatch,
):
    original = event(
        event_id=501,
        amount="6.67",
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_load_input_vat_"
            "fulfillment_bridge_events"
        ),
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    created = (
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(
                amount="6.66"
            ),
            currency_code="UAH",
            created_by=1,
            reversal_date=D2,
        )
    )

    assert len(
        created
    ) == 2

    reversal = created[0]
    replacement = created[1]

    assert (
        reversal.reversal_of_id
        == 501
    )

    assert (
        reversal.bridge_date
        == D2
    )

    assert (
        reversal.bridged_tax_amount
        == Decimal("6.67")
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.bridged_tax_amount
        == Decimal("6.66")
    )

    assert (
        replacement.bridge_date
        == D1
    )

    assert (
        db.add.call_count
        == 2
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_zero_target_can_reverse_reversed_allocation(
    monkeypatch,
):
    original = event(
        event_id=501,
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source(
                status=(
                    InvoiceFulfillmentAllocationStatus
                    .REVERSED
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_load_input_vat_"
            "fulfillment_bridge_events"
        ),
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    created = (
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(
                amount="0.00"
            ),
            currency_code="UAH",
            created_by=1,
            reversal_date=D2,
        )
    )

    assert len(
        created
    ) == 1

    assert (
        created[0].reversal_of_id
        == 501
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_positive_target_rejects_reversed_allocation(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source(
                status=(
                    InvoiceFulfillmentAllocationStatus
                    .REVERSED
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation()
        ),
    )

    with pytest.raises(
        InputVatFulfillmentBridgeSourceStateError,
        match="requires ACTIVE",
    ):
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_rejects_output_tax_calculation(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation(
                direction=TaxDirection.OUTPUT
            )
        ),
    )

    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="requires an INPUT",
    ):
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_reversal_date_cannot_precede_original(
    monkeypatch,
):
    original = event(
        event_id=501,
        bridge_date=D2,
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        AsyncMock(
            return_value=source()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        AsyncMock(
            return_value=calculation()
        ),
    )

    monkeypatch.setattr(
        service,
        (
            "_load_input_vat_"
            "fulfillment_bridge_events"
        ),
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="cannot precede",
    ):
        await reconcile_input_vat_fulfillment_bridge_source(
            db,
            company_id=1,
            target=target(
                amount="10.00",
                event_date=D2,
            ),
            currency_code="UAH",
            created_by=1,
            reversal_date=D1,
        )

    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_lock_order(
    monkeypatch,
):
    calls = []

    async def lock_source(
        db,
        *,
        company_id,
        source_id,
    ):
        calls.append(
            "source"
        )
        return source()

    async def lock_calculation(
        db,
        *,
        company_id,
        tax_calculation_id,
    ):
        calls.append(
            "calculation"
        )
        return calculation()

    async def load_events(
        db,
        *,
        company_id,
        source_id,
        lock_rows,
    ):
        assert lock_rows is True

        calls.append(
            "history"
        )

        return ()

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_source"
        ),
        lock_source,
    )

    monkeypatch.setattr(
        service,
        (
            "_lock_input_vat_"
            "fulfillment_bridge_tax_calculation"
        ),
        lock_calculation,
    )

    monkeypatch.setattr(
        service,
        (
            "_load_input_vat_"
            "fulfillment_bridge_events"
        ),
        load_events,
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    await reconcile_input_vat_fulfillment_bridge_source(
        db,
        company_id=1,
        target=target(),
        currency_code="UAH",
        created_by=1,
    )

    assert calls == [
        "source",
        "calculation",
        "history",
    ]
