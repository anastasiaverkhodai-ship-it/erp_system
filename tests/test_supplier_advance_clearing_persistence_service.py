from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.supplier_advance_clearing_persistence_service as service
from app.models.supplier_advance_clearing_event import (
    SupplierAdvanceClearingEvent,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.payment_types import (
    PaymentSettlementAllocationStatus,
)
from app.services.supplier_advance_clearing_calculation_service import (
    SupplierAdvanceClearingTarget,
)
from app.services.supplier_advance_clearing_persistence_service import (
    SupplierAdvanceClearingDataIntegrityError,
    SupplierAdvanceClearingSourceStateError,
    build_current_supplier_advance_clearing_targets,
    build_supplier_advance_clearing_source_plan,
    get_persistent_supplier_advance_clearing_target,
    reconcile_supplier_advance_clearing_source,
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
    amount: str = "60.00",
    settlement_source_id: int = 10,
    liability_source_id: int = 20,
    event_date: date = D1,
):
    return SupplierAdvanceClearingTarget(
        settlement_source_id=(
            settlement_source_id
        ),
        liability_source_id=(
            liability_source_id
        ),
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code="UAH",
    )


def event(
    *,
    event_id: int,
    amount: str = "60.00",
    settlement_source_id: int = 10,
    liability_source_id: int = 20,
    clearing_date: date = D1,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        payment_settlement_allocation_id=(
            settlement_source_id
        ),
        invoice_fulfillment_allocation_id=(
            liability_source_id
        ),
        clearing_date=clearing_date,
        cleared_amount=Decimal(
            amount
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def settlement_source(
    *,
    status=(
        PaymentSettlementAllocationStatus
        .ACTIVE
    ),
):
    return SimpleNamespace(
        company_id=1,
        id=10,
        status=status,
    )


def liability_source(
    *,
    status=(
        InvoiceFulfillmentAllocationStatus
        .ACTIVE
    ),
):
    return SimpleNamespace(
        company_id=1,
        id=20,
        status=status,
    )


def test_new_positive_target_creates_original_plan():
    plan = (
        build_supplier_advance_clearing_source_plan(
            events=(),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == target()


def test_exact_target_is_noop():
    plan = (
        build_supplier_advance_clearing_source_plan(
            events=(
                event(
                    event_id=1,
                ),
            ),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan.is_noop is True


def test_changed_amount_reverses_and_replaces():
    original = event(
        event_id=1,
        amount="40.00",
    )

    desired = target(
        amount="60.00",
    )

    plan = (
        build_supplier_advance_clearing_source_plan(
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
    plan = (
        build_supplier_advance_clearing_source_plan(
            events=(
                event(
                    event_id=1,
                ),
            ),
            target=target(
                amount="0.00",
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
    current = (
        build_current_supplier_advance_clearing_targets(
            events=(
                event(
                    event_id=1,
                ),
                event(
                    event_id=2,
                    clearing_date=D2,
                    reversal_of_id=1,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert current == ()


def test_replacement_becomes_current():
    current = (
        build_current_supplier_advance_clearing_targets(
            events=(
                event(
                    event_id=1,
                    amount="40.00",
                ),
                event(
                    event_id=2,
                    amount="40.00",
                    clearing_date=D2,
                    reversal_of_id=1,
                ),
                event(
                    event_id=3,
                    amount="60.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert len(
        current
    ) == 1

    assert (
        current[0].amount
        == Decimal("60.00")
    )


def test_duplicate_active_pair_fails_closed():
    with pytest.raises(
        SupplierAdvanceClearingDataIntegrityError,
        match="more than one active",
    ):
        build_current_supplier_advance_clearing_targets(
            events=(
                event(
                    event_id=1,
                ),
                event(
                    event_id=2,
                    amount="20.00",
                ),
            ),
            currency_code="UAH",
        )


def test_historical_event_date_cannot_change():
    original = event(
        event_id=1,
    )

    reversal = event(
        event_id=2,
        clearing_date=D2,
        reversal_of_id=1,
    )

    with pytest.raises(
        SupplierAdvanceClearingDataIntegrityError,
        match="historical event_date",
    ):
        build_supplier_advance_clearing_source_plan(
            events=(
                original,
                reversal,
            ),
            target=target(
                event_date=D2,
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
        "_lock_supplier_advance_settlement_source",
        AsyncMock(
            return_value=(
                settlement_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_economic_liability_source",
        AsyncMock(
            return_value=(
                liability_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        AsyncMock(
            return_value=()
        ),
    )

    created = (
        await reconcile_supplier_advance_clearing_source(
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
        SupplierAdvanceClearingEvent,
    )

    assert (
        original.reversal_of_id
        is None
    )

    assert (
        original.payment_settlement_allocation_id
        == 10
    )

    assert (
        original.invoice_fulfillment_allocation_id
        == 20
    )

    assert (
        original.cleared_amount
        == Decimal("60.00")
    )

    db.add.assert_called_once_with(
        original
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_changed_amount_reverses_and_replaces(
    monkeypatch,
):
    original = event(
        event_id=501,
        amount="40.00",
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_advance_settlement_source",
        AsyncMock(
            return_value=(
                settlement_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_economic_liability_source",
        AsyncMock(
            return_value=(
                liability_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    created = (
        await reconcile_supplier_advance_clearing_source(
            db,
            company_id=1,
            target=target(
                amount="60.00",
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
        reversal.clearing_date
        == D2
    )

    assert (
        reversal.cleared_amount
        == Decimal("40.00")
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.clearing_date
        == D1
    )

    assert (
        replacement.cleared_amount
        == Decimal("60.00")
    )

    assert (
        db.add.call_count
        == 2
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_exact_target_is_noop(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_advance_settlement_source",
        AsyncMock(
            return_value=(
                settlement_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_economic_liability_source",
        AsyncMock(
            return_value=(
                liability_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        AsyncMock(
            return_value=(
                event(
                    event_id=1,
                ),
            )
        ),
    )

    created = (
        await reconcile_supplier_advance_clearing_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )
    )

    assert created == ()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_target_can_reverse_after_source_state_change(
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
        "_lock_supplier_advance_settlement_source",
        AsyncMock(
            return_value=(
                settlement_source(
                    status="REVERSED",
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_economic_liability_source",
        AsyncMock(
            return_value=(
                liability_source(
                    status="REVERSED",
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    created = (
        await reconcile_supplier_advance_clearing_source(
            db,
            company_id=1,
            target=target(
                amount="0.00",
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

    assert (
        created[0].clearing_date
        == D2
    )

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_positive_target_requires_active_settlement_source(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_advance_settlement_source",
        AsyncMock(
            return_value=(
                settlement_source(
                    status="REVERSED",
                )
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_supplier_economic_liability_source",
        AsyncMock(
            return_value=(
                liability_source()
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        AsyncMock(
            return_value=()
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingSourceStateError,
    ):
        await reconcile_supplier_advance_clearing_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_persistent_target(
    monkeypatch,
):
    db = SimpleNamespace()

    loader = AsyncMock(
        return_value=(
            event(
                event_id=1,
            ),
        )
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_advance_clearing_events",
        loader,
    )

    current = (
        await get_persistent_supplier_advance_clearing_target(
            db,
            company_id=1,
            settlement_source_id=10,
            liability_source_id=20,
            currency_code="UAH",
        )
    )

    assert current == target()

    loader.assert_awaited_once_with(
        db,
        company_id=1,
        settlement_source_id=10,
        liability_source_id=20,
        lock_rows=False,
    )
