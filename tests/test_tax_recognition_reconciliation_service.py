from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.tax_recognition_persistence_service as persistence
import app.services.tax_recognition_reconciliation_service as service

from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    TaxRecognitionCandidateKind,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionInputEvidenceRequiredError,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)


def _calculation(
    *,
    direction=TaxDirection.OUTPUT,
    method=TaxRecognitionMethod.FIRST_EVENT,
):
    return SimpleNamespace(
        id=1,
        company_id=1,
        trade_document_id=100,
        trade_document_line_id=200,
        product_id=300,
        direction=direction,
        recognition_method=method,
        taxable_base=Decimal(
            "100.00"
        ),
        tax_amount=Decimal(
            "20.00"
        ),
        currency_code="UAH",
    )


def _event(
    *,
    event_id,
    event_date,
    base,
    tax,
    fulfillment_id=None,
    settlement_id=None,
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        recognition_date=event_date,
        recognized_taxable_base=Decimal(
            base
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        invoice_fulfillment_allocation_id=(
            fulfillment_id
        ),
        payment_settlement_allocation_id=(
            settlement_id
        ),
        reversal_of_id=reversal_of_id,
    )


def _candidate(
    *,
    kind,
    source_id,
    event_date,
    base,
    tax,
):
    return TaxRecognitionCandidate(
        kind=kind,
        source_id=source_id,
        event_date=event_date,
        taxable_base_capacity=Decimal(
            base
        ),
        tax_amount_capacity=Decimal(
            tax
        ),
    )


def test_current_targets_aggregate_active_increments():
    events = (
        _event(
            event_id=1,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="60",
            tax="12",
            settlement_id=20,
        ),
        _event(
            event_id=2,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="20",
            tax="4",
            settlement_id=20,
        ),
        _event(
            event_id=3,
            event_date=date(
                2026,
                8,
                11,
            ),
            base="20",
            tax="4",
            fulfillment_id=10,
        ),
        _event(
            event_id=4,
            event_date=date(
                2026,
                8,
                15,
            ),
            base="60",
            tax="12",
            settlement_id=20,
            reversal_of_id=1,
        ),
    )

    targets = (
        service
        .build_current_output_tax_recognition_targets(
            events
        )
    )

    assert len(
        targets
    ) == 2

    settlement = next(
        target
        for target in targets
        if (
            target.kind
            == TaxRecognitionCandidateKind
            .SETTLEMENT
        )
    )

    fulfillment = next(
        target
        for target in targets
        if (
            target.kind
            == TaxRecognitionCandidateKind
            .FULFILLMENT
        )
    )

    assert (
        settlement.taxable_base
        == Decimal("20")
    )

    assert (
        settlement.tax_amount
        == Decimal("4")
    )

    assert (
        settlement.event_date
        == date(
            2026,
            8,
            10,
        )
    )

    assert (
        fulfillment.taxable_base
        == Decimal("20")
    )


def test_current_target_rejects_source_less_original():
    events = (
        _event(
            event_id=1,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="10",
            tax="2",
        ),
    )

    with pytest.raises(
        persistence
        .TaxRecognitionDataIntegrityError
    ):
        (
            service
            .build_current_output_tax_recognition_targets(
                events
            )
        )


@pytest.mark.asyncio
async def test_reconciliation_decreases_before_increases(
    monkeypatch,
):
    calculation = _calculation()

    current_events = (
        _event(
            event_id=1,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="60",
            tax="12",
            settlement_id=20,
        ),
        _event(
            event_id=2,
            event_date=date(
                2026,
                8,
                11,
            ),
            base="40",
            tax="8",
            fulfillment_id=10,
        ),
    )

    desired_candidates = (
        _candidate(
            kind=(
                TaxRecognitionCandidateKind
                .FULFILLMENT
            ),
            source_id=10,
            event_date=date(
                2026,
                8,
                11,
            ),
            base="70",
            tax="14",
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_tax_calculation",
        AsyncMock(
            return_value=calculation
        ),
    )

    monkeypatch.setattr(
        service,
        "load_active_output_tax_recognition_candidates",
        AsyncMock(
            return_value=desired_candidates
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_tax_recognition_events",
        AsyncMock(
            return_value=current_events
        ),
    )

    reconcile_mock = AsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_recognition_source",
        reconcile_mock,
    )

    result = await (
        service
        .reconcile_output_tax_calculation_from_active_sources(
            SimpleNamespace(),
            company_id=1,
            tax_calculation_id=1,
            adjustment_date=date(
                2026,
                8,
                15,
            ),
            created_by=99,
        )
    )

    assert len(
        result.adjustments
    ) == 2

    assert (
        reconcile_mock.await_count
        == 2
    )

    first = (
        reconcile_mock
        .await_args_list[0]
        .kwargs
    )

    second = (
        reconcile_mock
        .await_args_list[1]
        .kwargs
    )

    assert (
        first[
            "payment_settlement_allocation_id"
        ]
        == 20
    )

    assert (
        first[
            "target_taxable_base"
        ]
        == Decimal("0")
    )

    assert (
        first[
            "recognition_date"
        ]
        == date(
            2026,
            8,
            10,
        )
    )

    assert (
        first[
            "reversal_date"
        ]
        == date(
            2026,
            8,
            15,
        )
    )

    assert (
        second[
            "invoice_fulfillment_allocation_id"
        ]
        == 10
    )

    assert (
        second[
            "target_taxable_base"
        ]
        == Decimal("70")
    )

    assert (
        second[
            "recognition_date"
        ]
        == date(
            2026,
            8,
            11,
        )
    )


@pytest.mark.asyncio
async def test_reconciliation_noop(
    monkeypatch,
):
    calculation = _calculation()

    events = (
        _event(
            event_id=1,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="50",
            tax="10",
            settlement_id=20,
        ),
    )

    candidates = (
        _candidate(
            kind=(
                TaxRecognitionCandidateKind
                .SETTLEMENT
            ),
            source_id=20,
            event_date=date(
                2026,
                8,
                10,
            ),
            base="50",
            tax="10",
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_tax_calculation",
        AsyncMock(
            return_value=calculation
        ),
    )

    monkeypatch.setattr(
        service,
        "load_active_output_tax_recognition_candidates",
        AsyncMock(
            return_value=candidates
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_tax_recognition_events",
        AsyncMock(
            return_value=events
        ),
    )

    reconcile_mock = AsyncMock()

    monkeypatch.setattr(
        service,
        "reconcile_output_tax_recognition_source",
        reconcile_mock,
    )

    result = await (
        service
        .reconcile_output_tax_calculation_from_active_sources(
            SimpleNamespace(),
            company_id=1,
            tax_calculation_id=1,
            adjustment_date=date(
                2026,
                8,
                15,
            ),
            created_by=99,
        )
    )

    assert (
        result.adjustments
        == ()
    )

    reconcile_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_fails_before_candidate_loading(
    monkeypatch,
):
    calculation = _calculation(
        direction=TaxDirection.INPUT
    )

    monkeypatch.setattr(
        service,
        "_lock_tax_calculation",
        AsyncMock(
            return_value=calculation
        ),
    )

    candidate_loader = AsyncMock()

    monkeypatch.setattr(
        service,
        "load_active_output_tax_recognition_candidates",
        candidate_loader,
    )

    with pytest.raises(
        TaxRecognitionInputEvidenceRequiredError
    ):
        await (
            service
            .reconcile_output_tax_calculation_from_active_sources(
                SimpleNamespace(),
                company_id=1,
                tax_calculation_id=1,
                adjustment_date=date(
                    2026,
                    8,
                    15,
                ),
                created_by=99,
            )
        )

    candidate_loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistence_uses_separate_reversal_date(
    monkeypatch,
):
    calculation = _calculation()

    original = _event(
        event_id=1,
        event_date=date(
            2026,
            8,
            10,
        ),
        base="60",
        tax="12",
        settlement_id=20,
    )

    monkeypatch.setattr(
        persistence,
        "_lock_tax_calculation",
        AsyncMock(
            return_value=calculation
        ),
    )

    monkeypatch.setattr(
        persistence,
        "_load_events",
        AsyncMock(
            return_value=(
                original,
            )
        ),
    )

    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    created = await (
        persistence
        .reconcile_output_tax_recognition_source(
            db,
            company_id=1,
            tax_calculation_id=1,
            recognition_date=date(
                2026,
                8,
                10,
            ),
            reversal_date=date(
                2026,
                8,
                15,
            ),
            target_taxable_base=Decimal(
                "0"
            ),
            target_tax_amount=Decimal(
                "0"
            ),
            created_by=99,
            payment_settlement_allocation_id=20,
        )
    )

    assert len(
        created
    ) == 1

    reversal = created[0]

    assert (
        reversal.reversal_of_id
        == 1
    )

    assert (
        reversal.recognition_date
        == date(
            2026,
            8,
            15,
        )
    )

    assert (
        reversal
        .payment_settlement_allocation_id
        == 20
    )

    db.flush.assert_awaited_once()
