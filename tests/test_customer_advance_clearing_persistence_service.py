from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.customer_advance_clearing_persistence_service as service

from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
)
from app.services.customer_advance_clearing_calculation_service import (
    CustomerAdvanceClearingTarget,
)
from app.services.customer_advance_clearing_persistence_service import (
    CustomerAdvanceClearingDataIntegrityError,
    CustomerAdvanceClearingSourcePlan,
    build_current_customer_advance_clearing_targets,
    build_customer_advance_clearing_source_plan,
    reconcile_customer_advance_clearing_source,
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
    settlement_id=10,
    receivable_id=20,
    event_date=D2,
    amount="60.00",
    currency="UAH",
):
    return CustomerAdvanceClearingTarget(
        settlement_source_id=settlement_id,
        receivable_source_id=receivable_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code=currency,
    )


def event(
    *,
    event_id,
    settlement_id=10,
    receivable_id=20,
    clearing_date=D2,
    amount="60.00",
    currency="UAH",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        payment_settlement_allocation_id=settlement_id,
        sales_recognition_event_id=receivable_id,
        clearing_date=clearing_date,
        cleared_amount=Decimal(
            amount
        ),
        currency_code=currency,
        reversal_of_id=reversal_of_id,
    )


def test_new_positive_target_creates_full_original_plan():
    desired = target()

    plan = (
        build_customer_advance_clearing_source_plan(
            events=(),
            target=desired,
            currency_code="UAH",
        )
    )

    assert plan == CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(),
        replacement_target=desired,
    )


def test_exact_current_target_is_noop():
    plan = (
        build_customer_advance_clearing_source_plan(
            events=(
                event(
                    event_id=100,
                ),
            ),
            target=target(),
            currency_code="UAH",
        )
    )

    assert plan == CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(),
        replacement_target=None,
    )


def test_changed_positive_target_reverses_and_replaces():
    desired = target(
        amount="40.00",
    )

    plan = (
        build_customer_advance_clearing_source_plan(
            events=(
                event(
                    event_id=100,
                    amount="60.00",
                ),
            ),
            target=desired,
            currency_code="UAH",
        )
    )

    assert plan == CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(
            100,
        ),
        replacement_target=desired,
    )


def test_zero_target_reverses_active_original_only():
    zero_target = target(
        amount="0.00",
    )

    plan = (
        build_customer_advance_clearing_source_plan(
            events=(
                event(
                    event_id=100,
                ),
            ),
            target=zero_target,
            currency_code="UAH",
        )
    )

    assert plan == CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(
            100,
        ),
        replacement_target=None,
    )


def test_new_zero_target_is_noop():
    plan = (
        build_customer_advance_clearing_source_plan(
            events=(),
            target=target(
                amount="0.00",
            ),
            currency_code="UAH",
        )
    )

    assert plan == CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(),
        replacement_target=None,
    )


def test_current_state_ignores_reversed_original():
    result = (
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                ),
                event(
                    event_id=101,
                    clearing_date=D3,
                    reversal_of_id=100,
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == ()


def test_current_state_uses_replacement_after_reversal():
    result = (
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                    amount="60.00",
                ),
                event(
                    event_id=101,
                    amount="60.00",
                    clearing_date=D3,
                    reversal_of_id=100,
                ),
                event(
                    event_id=102,
                    amount="40.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        target(
            amount="40.00",
        ),
    )


def test_current_state_supports_multiple_source_pairs():
    result = (
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                    settlement_id=10,
                    receivable_id=20,
                    amount="30.00",
                ),
                event(
                    event_id=101,
                    settlement_id=11,
                    receivable_id=21,
                    clearing_date=D3,
                    amount="50.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert [
        (
            item.settlement_source_id,
            item.receivable_source_id,
            item.amount,
        )
        for item in result
    ] == [
        (
            10,
            20,
            Decimal("30.00"),
        ),
        (
            11,
            21,
            Decimal("50.00"),
        ),
    ]


def test_reversal_must_preserve_source_pair():
    with pytest.raises(
        CustomerAdvanceClearingDataIntegrityError
    ):
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                ),
                event(
                    event_id=101,
                    receivable_id=21,
                    clearing_date=D3,
                    reversal_of_id=100,
                ),
            ),
            currency_code="UAH",
        )


def test_reversal_must_preserve_amount():
    with pytest.raises(
        CustomerAdvanceClearingDataIntegrityError
    ):
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                    amount="60.00",
                ),
                event(
                    event_id=101,
                    amount="59.00",
                    clearing_date=D3,
                    reversal_of_id=100,
                ),
            ),
            currency_code="UAH",
        )


def test_reversal_cannot_precede_original():
    with pytest.raises(
        CustomerAdvanceClearingDataIntegrityError
    ):
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                    clearing_date=D2,
                ),
                event(
                    event_id=101,
                    clearing_date=D1,
                    reversal_of_id=100,
                ),
            ),
            currency_code="UAH",
        )


def test_more_than_one_active_original_for_pair_rejected():
    with pytest.raises(
        CustomerAdvanceClearingDataIntegrityError
    ):
        build_current_customer_advance_clearing_targets(
            events=(
                event(
                    event_id=100,
                ),
                event(
                    event_id=101,
                ),
            ),
            currency_code="UAH",
        )


def test_changed_provenance_date_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingDataIntegrityError
    ):
        build_customer_advance_clearing_source_plan(
            events=(
                event(
                    event_id=100,
                    clearing_date=D2,
                ),
            ),
            target=target(
                event_date=D3,
                amount="40.00",
            ),
            currency_code="UAH",
        )


class FakeDb:
    def __init__(self):
        self.added = []
        self.flush_count = 0

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
        self.flush_count += 1


def settlement(
    *,
    amount="120.00",
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        amount=Decimal(
            amount
        ),
    )


def receivable(
    *,
    amount="120.00",
):
    return SimpleNamespace(
        id=20,
        company_id=1,
        recognized_gross_amount=Decimal(
            amount
        ),
    )


def patch_sources(
    monkeypatch,
    *,
    history=(),
):
    async def fake_lock_settlement(
        db,
        *,
        company_id,
        settlement_source_id,
        currency_code,
        require_active,
    ):
        assert company_id == 1
        assert settlement_source_id == 10
        assert currency_code == "UAH"

        return settlement()

    async def fake_lock_receivable(
        db,
        *,
        company_id,
        receivable_source_id,
        currency_code,
        require_active,
    ):
        assert company_id == 1
        assert receivable_source_id == 20
        assert currency_code == "UAH"

        return receivable()

    async def fake_load(
        db,
        *,
        company_id,
        settlement_source_id,
        receivable_source_id,
        lock_rows,
    ):
        assert lock_rows is True

        return tuple(
            history
        )

    monkeypatch.setattr(
        service,
        "_lock_customer_settlement_source",
        fake_lock_settlement,
    )

    monkeypatch.setattr(
        service,
        "_lock_customer_economic_receivable_source",
        fake_lock_receivable,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_advance_clearing_events",
        fake_load,
    )


@pytest.mark.asyncio
async def test_reconcile_new_positive_creates_original(
    monkeypatch,
):
    patch_sources(
        monkeypatch,
        history=(),
    )

    db = FakeDb()

    created = (
        await reconcile_customer_advance_clearing_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )
    )

    assert len(created) == 1

    original = created[0]

    assert isinstance(
        original,
        CustomerAdvanceClearingEvent,
    )

    assert original.reversal_of_id is None
    assert (
        original
        .payment_settlement_allocation_id
        == 10
    )
    assert (
        original
        .sales_recognition_event_id
        == 20
    )
    assert (
        original.cleared_amount
        == Decimal("60.00")
    )


@pytest.mark.asyncio
async def test_reconcile_exact_target_is_noop(
    monkeypatch,
):
    patch_sources(
        monkeypatch,
        history=(
            event(
                event_id=100,
            ),
        ),
    )

    db = FakeDb()

    created = (
        await reconcile_customer_advance_clearing_source(
            db,
            company_id=1,
            target=target(),
            currency_code="UAH",
            created_by=1,
        )
    )

    assert created == ()
    assert db.added == []


@pytest.mark.asyncio
async def test_reconcile_changed_target_creates_reversal_then_replacement(
    monkeypatch,
):
    original = event(
        event_id=100,
        amount="60.00",
    )

    patch_sources(
        monkeypatch,
        history=(
            original,
        ),
    )

    db = FakeDb()

    created = (
        await reconcile_customer_advance_clearing_source(
            db,
            company_id=1,
            target=target(
                amount="40.00",
            ),
            currency_code="UAH",
            created_by=1,
            reversal_date=D3,
        )
    )

    assert len(created) == 2

    reversal = created[0]
    replacement = created[1]

    assert reversal.reversal_of_id == 100
    assert reversal.clearing_date == D3
    assert (
        reversal.cleared_amount
        == Decimal("60.00")
    )

    assert replacement.reversal_of_id is None
    assert replacement.clearing_date == D2
    assert (
        replacement.cleared_amount
        == Decimal("40.00")
    )


@pytest.mark.asyncio
async def test_reconcile_zero_creates_only_reversal(
    monkeypatch,
):
    patch_sources(
        monkeypatch,
        history=(
            event(
                event_id=100,
            ),
        ),
    )

    db = FakeDb()

    created = (
        await reconcile_customer_advance_clearing_source(
            db,
            company_id=1,
            target=target(
                amount="0.00",
            ),
            currency_code="UAH",
            created_by=1,
            reversal_date=D3,
        )
    )

    assert len(created) == 1
    assert created[0].reversal_of_id == 100


@pytest.mark.asyncio
async def test_zero_reconciliation_requests_inactive_sources_allowed(
    monkeypatch,
):
    seen = {}

    async def fake_lock_settlement(
        db,
        *,
        company_id,
        settlement_source_id,
        currency_code,
        require_active,
    ):
        seen[
            "settlement_require_active"
        ] = require_active

        return settlement()

    async def fake_lock_receivable(
        db,
        *,
        company_id,
        receivable_source_id,
        currency_code,
        require_active,
    ):
        seen[
            "receivable_require_active"
        ] = require_active

        return receivable()

    async def fake_load(
        *args,
        **kwargs,
    ):
        return (
            event(
                event_id=100,
            ),
        )

    monkeypatch.setattr(
        service,
        "_lock_customer_settlement_source",
        fake_lock_settlement,
    )

    monkeypatch.setattr(
        service,
        "_lock_customer_economic_receivable_source",
        fake_lock_receivable,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_advance_clearing_events",
        fake_load,
    )

    await reconcile_customer_advance_clearing_source(
        FakeDb(),
        company_id=1,
        target=target(
            amount="0.00",
        ),
        currency_code="UAH",
        created_by=1,
        reversal_date=D3,
    )

    assert (
        seen[
            "settlement_require_active"
        ]
        is False
    )

    assert (
        seen[
            "receivable_require_active"
        ]
        is False
    )


@pytest.mark.asyncio
async def test_positive_reconciliation_requires_active_sources(
    monkeypatch,
):
    seen = {}

    async def fake_lock_settlement(
        db,
        *,
        company_id,
        settlement_source_id,
        currency_code,
        require_active,
    ):
        seen[
            "settlement_require_active"
        ] = require_active

        return settlement()

    async def fake_lock_receivable(
        db,
        *,
        company_id,
        receivable_source_id,
        currency_code,
        require_active,
    ):
        seen[
            "receivable_require_active"
        ] = require_active

        return receivable()

    async def fake_load(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_lock_customer_settlement_source",
        fake_lock_settlement,
    )

    monkeypatch.setattr(
        service,
        "_lock_customer_economic_receivable_source",
        fake_lock_receivable,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_advance_clearing_events",
        fake_load,
    )

    await reconcile_customer_advance_clearing_source(
        FakeDb(),
        company_id=1,
        target=target(),
        currency_code="UAH",
        created_by=1,
    )

    assert (
        seen[
            "settlement_require_active"
        ]
        is True
    )

    assert (
        seen[
            "receivable_require_active"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_target_cannot_exceed_settlement_capacity(
    monkeypatch,
):
    async def fake_settlement(
        *args,
        **kwargs,
    ):
        return settlement(
            amount="50.00",
        )

    async def fake_receivable(
        *args,
        **kwargs,
    ):
        return receivable(
            amount="120.00",
        )

    monkeypatch.setattr(
        service,
        "_lock_customer_settlement_source",
        fake_settlement,
    )

    monkeypatch.setattr(
        service,
        "_lock_customer_economic_receivable_source",
        fake_receivable,
    )

    with pytest.raises(
        service.CustomerAdvanceClearingSourceStateError
    ):
        await reconcile_customer_advance_clearing_source(
            FakeDb(),
            company_id=1,
            target=target(
                amount="60.00",
            ),
            currency_code="UAH",
            created_by=1,
        )


@pytest.mark.asyncio
async def test_target_cannot_exceed_receivable_capacity(
    monkeypatch,
):
    async def fake_settlement(
        *args,
        **kwargs,
    ):
        return settlement(
            amount="120.00",
        )

    async def fake_receivable(
        *args,
        **kwargs,
    ):
        return receivable(
            amount="50.00",
        )

    monkeypatch.setattr(
        service,
        "_lock_customer_settlement_source",
        fake_settlement,
    )

    monkeypatch.setattr(
        service,
        "_lock_customer_economic_receivable_source",
        fake_receivable,
    )

    with pytest.raises(
        service.CustomerAdvanceClearingSourceStateError
    ):
        await reconcile_customer_advance_clearing_source(
            FakeDb(),
            company_id=1,
            target=target(
                amount="60.00",
            ),
            currency_code="UAH",
            created_by=1,
        )
