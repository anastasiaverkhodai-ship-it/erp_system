from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.customer_advance_clearing_reconciliation_service as service

from app.services.customer_advance_clearing_calculation_service import (
    CustomerAdvanceClearingTarget,
    CustomerAdvanceSettlementCandidate,
    CustomerEconomicReceivableCandidate,
)
from app.services.customer_advance_clearing_reconciliation_service import (
    CustomerAdvanceClearingReconciliationDataIntegrityError,
    build_customer_advance_clearing_reconciliation_targets,
    reconcile_customer_advance_clearing_for_invoice,
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
    settlement_id,
    receivable_id,
    amount,
    event_date=D2,
):
    return CustomerAdvanceClearingTarget(
        settlement_source_id=settlement_id,
        receivable_source_id=receivable_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code="UAH",
    )


def test_exact_targets_are_omitted():
    existing = target(
        settlement_id=10,
        receivable_id=20,
        amount="60.00",
    )

    result = (
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=(
                existing,
            ),
            current_targets=(
                existing,
            ),
        )
    )

    assert result == ()


def test_missing_desired_pair_becomes_zero_target():
    result = (
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=(),
            current_targets=(
                target(
                    settlement_id=10,
                    receivable_id=20,
                    amount="60.00",
                ),
            ),
        )
    )

    assert len(result) == 1
    assert result[0].amount == Decimal(
        "0.00"
    )


def test_new_pair_becomes_positive_target():
    desired = target(
        settlement_id=10,
        receivable_id=20,
        amount="60.00",
    )

    result = (
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(),
        )
    )

    assert result == (
        desired,
    )


def test_decrease_is_scheduled_before_increase():
    current = (
        target(
            settlement_id=10,
            receivable_id=20,
            amount="60.00",
            event_date=D1,
        ),
        target(
            settlement_id=11,
            receivable_id=21,
            amount="20.00",
            event_date=D2,
        ),
    )

    desired = (
        target(
            settlement_id=10,
            receivable_id=20,
            amount="40.00",
            event_date=D1,
        ),
        target(
            settlement_id=11,
            receivable_id=21,
            amount="40.00",
            event_date=D2,
        ),
    )

    result = (
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=desired,
            current_targets=current,
        )
    )

    assert [
        item.amount
        for item in result
    ] == [
        Decimal("40.00"),
        Decimal("40.00"),
    ]

    assert (
        result[0]
        .settlement_source_id
        == 10
    )

    assert (
        result[1]
        .settlement_source_id
        == 11
    )


def test_removal_is_scheduled_before_new_pair():
    result = (
        build_customer_advance_clearing_reconciliation_targets(
            current_targets=(
                target(
                    settlement_id=10,
                    receivable_id=20,
                    amount="60.00",
                    event_date=D1,
                ),
            ),
            desired_targets=(
                target(
                    settlement_id=10,
                    receivable_id=21,
                    amount="60.00",
                    event_date=D3,
                ),
            ),
        )
    )

    assert len(result) == 2

    assert result[0].amount == Decimal(
        "0.00"
    )

    assert (
        result[0]
        .receivable_source_id
        == 20
    )

    assert result[1].amount == Decimal(
        "60.00"
    )

    assert (
        result[1]
        .receivable_source_id
        == 21
    )


def test_same_pair_provenance_date_change_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingReconciliationDataIntegrityError
    ):
        build_customer_advance_clearing_reconciliation_targets(
            current_targets=(
                target(
                    settlement_id=10,
                    receivable_id=20,
                    amount="60.00",
                    event_date=D1,
                ),
            ),
            desired_targets=(
                target(
                    settlement_id=10,
                    receivable_id=20,
                    amount="40.00",
                    event_date=D2,
                ),
            ),
        )


def test_duplicate_desired_pair_is_rejected():
    duplicate = target(
        settlement_id=10,
        receivable_id=20,
        amount="60.00",
    )

    with pytest.raises(
        CustomerAdvanceClearingReconciliationDataIntegrityError
    ):
        build_customer_advance_clearing_reconciliation_targets(
            desired_targets=(
                duplicate,
                duplicate,
            ),
            current_targets=(),
        )


class FakeDb:
    pass


@pytest.mark.asyncio
async def test_invoice_reconciliation_payment_first_has_no_clearing(
    monkeypatch,
):
    open_item = SimpleNamespace(
        id=500,
        currency_code="UAH",
    )

    settlement_candidates = (
        CustomerAdvanceSettlementCandidate(
            source_id=10,
            event_date=D1,
            amount=Decimal(
                "120.00"
            ),
            currency_code="UAH",
        ),
    )

    async def fake_open_item(
        *args,
        **kwargs,
    ):
        return open_item

    async def fake_settlements(
        *args,
        **kwargs,
    ):
        return settlement_candidates

    async def fake_receivables(
        *args,
        **kwargs,
    ):
        return ()

    async def fake_history(
        *args,
        **kwargs,
    ):
        return ()

    async def fail_persistence(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "No persistence should occur"
        )

    monkeypatch.setattr(
        service,
        "_load_receivable_open_item",
        fake_open_item,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_settlement_candidates",
        fake_settlements,
    )

    monkeypatch.setattr(
        service,
        "load_customer_economic_receivable_candidates_for_invoice",
        fake_receivables,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_clearing_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_source",
        fail_persistence,
    )

    result = (
        await reconcile_customer_advance_clearing_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=100,
            created_by=1,
        )
    )

    assert result.desired_targets == ()
    assert result.created_events == ()


@pytest.mark.asyncio
async def test_invoice_reconciliation_payment_then_receivable_creates_60(
    monkeypatch,
):
    open_item = SimpleNamespace(
        id=500,
        currency_code="UAH",
    )

    settlement_candidates = (
        CustomerAdvanceSettlementCandidate(
            source_id=10,
            event_date=D1,
            amount=Decimal(
                "120.00"
            ),
            currency_code="UAH",
        ),
    )

    receivable_candidates = (
        CustomerEconomicReceivableCandidate(
            source_id=20,
            event_date=D2,
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    async def fake_open_item(
        *args,
        **kwargs,
    ):
        return open_item

    async def fake_settlements(
        *args,
        **kwargs,
    ):
        return settlement_candidates

    async def fake_receivables(
        *args,
        **kwargs,
    ):
        return receivable_candidates

    async def fake_history(
        *args,
        **kwargs,
    ):
        return ()

    seen = []

    async def fake_persistence(
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
                id=900,
            ),
        )

    monkeypatch.setattr(
        service,
        "_load_receivable_open_item",
        fake_open_item,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_settlement_candidates",
        fake_settlements,
    )

    monkeypatch.setattr(
        service,
        "load_customer_economic_receivable_candidates_for_invoice",
        fake_receivables,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_clearing_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_source",
        fake_persistence,
    )

    result = (
        await reconcile_customer_advance_clearing_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=100,
            created_by=1,
        )
    )

    assert len(
        result.desired_targets
    ) == 1

    assert (
        result
        .desired_targets[0]
        .amount
        == Decimal("60.00")
    )

    assert seen == list(
        result.reconciliation_targets
    )

    assert len(
        result.created_events
    ) == 1


@pytest.mark.asyncio
async def test_invoice_reconciliation_executes_removal_before_new_pair(
    monkeypatch,
):
    open_item = SimpleNamespace(
        id=500,
        currency_code="UAH",
    )

    settlements = (
        CustomerAdvanceSettlementCandidate(
            source_id=10,
            event_date=D1,
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    receivables = (
        CustomerEconomicReceivableCandidate(
            source_id=21,
            event_date=D3,
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    async def fake_open_item(
        *args,
        **kwargs,
    ):
        return open_item

    async def fake_settlements(
        *args,
        **kwargs,
    ):
        return settlements

    async def fake_receivables(
        *args,
        **kwargs,
    ):
        return receivables

    old_event = SimpleNamespace(
        id=1000,
        company_id=1,
        payment_settlement_allocation_id=10,
        sales_recognition_event_id=20,
        clearing_date=D2,
        cleared_amount=Decimal(
            "60.00"
        ),
        currency_code="UAH",
        reversal_of_id=None,
    )

    async def fake_history(
        *args,
        **kwargs,
    ):
        return (
            old_event,
        )

    calls = []

    async def fake_persistence(
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
                target.receivable_source_id,
                target.amount,
            )
        )

        return ()

    monkeypatch.setattr(
        service,
        "_load_receivable_open_item",
        fake_open_item,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_settlement_candidates",
        fake_settlements,
    )

    monkeypatch.setattr(
        service,
        "load_customer_economic_receivable_candidates_for_invoice",
        fake_receivables,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_clearing_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_source",
        fake_persistence,
    )

    await reconcile_customer_advance_clearing_for_invoice(
        FakeDb(),
        company_id=1,
        invoice_id=100,
        created_by=1,
        adjustment_date=D3,
    )

    assert calls == [
        (
            20,
            Decimal("0.00"),
        ),
        (
            21,
            Decimal("60.00"),
        ),
    ]


@pytest.mark.asyncio
async def test_invoice_reconciliation_two_receivables_produces_60_60(
    monkeypatch,
):
    open_item = SimpleNamespace(
        id=500,
        currency_code="UAH",
    )

    settlements = (
        CustomerAdvanceSettlementCandidate(
            source_id=10,
            event_date=D1,
            amount=Decimal(
                "120.00"
            ),
            currency_code="UAH",
        ),
    )

    receivables = (
        CustomerEconomicReceivableCandidate(
            source_id=20,
            event_date=D2,
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
        CustomerEconomicReceivableCandidate(
            source_id=21,
            event_date=D3,
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )

    async def fake_open_item(
        *args,
        **kwargs,
    ):
        return open_item

    async def fake_settlements(
        *args,
        **kwargs,
    ):
        return settlements

    async def fake_receivables(
        *args,
        **kwargs,
    ):
        return receivables

    async def fake_history(
        *args,
        **kwargs,
    ):
        return ()

    async def fake_persistence(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_receivable_open_item",
        fake_open_item,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_settlement_candidates",
        fake_settlements,
    )

    monkeypatch.setattr(
        service,
        "load_customer_economic_receivable_candidates_for_invoice",
        fake_receivables,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_clearing_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_customer_advance_clearing_source",
        fake_persistence,
    )

    result = (
        await reconcile_customer_advance_clearing_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=100,
            created_by=1,
        )
    )

    assert [
        item.amount
        for item in result.desired_targets
    ] == [
        Decimal("60.00"),
        Decimal("60.00"),
    ]


@pytest.mark.asyncio
async def test_invoice_open_item_currency_controls_reconciliation(
    monkeypatch,
):
    open_item = SimpleNamespace(
        id=500,
        currency_code="UAH",
    )

    async def fake_open_item(
        *args,
        **kwargs,
    ):
        return open_item

    async def fake_settlements(
        *args,
        **kwargs,
    ):
        return ()

    async def fake_receivables(
        *args,
        **kwargs,
    ):
        return (
            CustomerEconomicReceivableCandidate(
                source_id=20,
                event_date=D2,
                amount=Decimal(
                    "60.00"
                ),
                currency_code="USD",
            ),
        )

    monkeypatch.setattr(
        service,
        "_load_receivable_open_item",
        fake_open_item,
    )

    monkeypatch.setattr(
        service,
        "_load_customer_settlement_candidates",
        fake_settlements,
    )

    monkeypatch.setattr(
        service,
        "load_customer_economic_receivable_candidates_for_invoice",
        fake_receivables,
    )

    with pytest.raises(
        CustomerAdvanceClearingReconciliationDataIntegrityError
    ):
        await reconcile_customer_advance_clearing_for_invoice(
            FakeDb(),
            company_id=1,
            invoice_id=100,
            created_by=1,
        )
