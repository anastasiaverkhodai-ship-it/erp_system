from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.sales_recognition_reconciliation_service as service
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionCandidate,
    SalesRecognitionDataIntegrityError,
    SalesRecognitionTarget,
)
from app.services.sales_recognition_reconciliation_service import (
    SalesRecognitionInvoiceLineStateError,
    build_sales_recognition_invoice_line_snapshot,
)
from app.services.tax_types import (
    TaxDirection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


D1 = date(2026, 9, 1)

D2 = date(2026, 9, 2)


def _document():
    return SimpleNamespace(
        id=100,
        company_id=1,
        direction=TradeDirection.SALE,
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        currency_code="UAH",
    )


def _line(
    *,
    tax_rate_code="20",
    quantity="3",
    unit_price="100",
):
    return SimpleNamespace(
        id=200,
        company_id=1,
        trade_document_id=100,
        product_id=300,
        quantity=Decimal(
            quantity
        ),
        unit_price=Decimal(
            unit_price
        ),
        tax_rate_code=tax_rate_code,
    )


def _calculation(
    *,
    base="250.00",
    tax="50.00",
):
    return SimpleNamespace(
        company_id=1,
        trade_document_id=100,
        trade_document_line_id=200,
        product_id=300,
        direction=TaxDirection.OUTPUT,
        taxable_base=Decimal(
            base
        ),
        tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
    )


def test_vat_snapshot_uses_persistent_base_plus_tax():
    snapshot = (
        build_sales_recognition_invoice_line_snapshot(
            document=_document(),
            line=_line(),
            calculation=_calculation(),
        )
    )

    assert (
        snapshot.quantity
        == Decimal("3")
    )

    assert (
        snapshot.gross_amount
        == Decimal("300.00")
    )

    assert (
        snapshot.tax_amount
        == Decimal("50.00")
    )


def test_non_vat_snapshot_uses_confirmed_invoice_line_amount():
    snapshot = (
        build_sales_recognition_invoice_line_snapshot(
            document=_document(),
            line=_line(
                tax_rate_code=None,
                quantity="3",
                unit_price="33.3333",
            ),
            calculation=None,
        )
    )

    assert (
        snapshot.gross_amount
        == Decimal("100.00")
    )

    assert (
        snapshot.tax_amount
        == Decimal("0.00")
    )


def test_vat_configured_line_requires_tax_snapshot():
    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="has no immutable TaxCalculation",
    ):
        build_sales_recognition_invoice_line_snapshot(
            document=_document(),
            line=_line(),
            calculation=None,
        )


def test_non_vat_line_rejects_unexpected_tax_snapshot():
    with pytest.raises(
        SalesRecognitionDataIntegrityError,
        match="unexpectedly has TaxCalculation",
    ):
        build_sales_recognition_invoice_line_snapshot(
            document=_document(),
            line=_line(
                tax_rate_code=None,
            ),
            calculation=_calculation(),
        )


def test_snapshot_requires_confirmed_sales_invoice():
    document = _document()
    document.status = TradeDocumentStatus.DRAFT

    with pytest.raises(
        SalesRecognitionInvoiceLineStateError,
        match="confirmed",
    ):
        build_sales_recognition_invoice_line_snapshot(
            document=document,
            line=_line(),
            calculation=_calculation(),
        )


@pytest.mark.asyncio
async def test_reconciliation_builds_targets_from_invoice_truth(
    monkeypatch,
):
    async def fake_context(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            _document(),
            _line(),
            _calculation(),
        )

    async def fake_candidates(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            SalesRecognitionCandidate(
                source_id=10,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=20,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        )

    async def fake_events(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return ()

    calls = []

    async def fake_reconcile_source(
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
                target,
                currency_code,
                reversal_date,
            )
        )
        return ()

    monkeypatch.setattr(
        service,
        "_lock_sales_invoice_line_context",
        fake_context,
    )

    monkeypatch.setattr(
        service,
        "_load_active_sales_recognition_candidates",
        fake_candidates,
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_sales_recognition_events",
        fake_events,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_source",
        fake_reconcile_source,
    )

    result = (
        await service
        .reconcile_sales_recognition_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=100,
            invoice_line_id=200,
            adjustment_date=D2,
            created_by=1,
        )
    )

    assert [
        target.source_id
        for target in result.desired_targets
    ] == [
        10,
        20,
    ]

    assert [
        target.quantity
        for target in result.desired_targets
    ] == [
        Decimal("1"),
        Decimal("1"),
    ]

    assert [
        target.gross_amount
        for target in result.desired_targets
    ] == [
        Decimal("100.00"),
        Decimal("100.00"),
    ]

    assert [
        target.tax_amount
        for target in result.desired_targets
    ] == [
        Decimal("16.67"),
        Decimal("16.66"),
    ]

    assert [
        call[0].source_id
        for call in calls
    ] == [
        10,
        20,
    ]

    assert all(
        call[1] == "UAH"
        for call in calls
    )

    assert all(
        call[2] == D2
        for call in calls
    )


@pytest.mark.asyncio
async def test_reconciliation_orders_removed_before_penny_growth(
    monkeypatch,
):
    async def fake_context(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            _document(),
            _line(),
            _calculation(),
        )

    async def fake_candidates(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            SalesRecognitionCandidate(
                source_id=1,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            SalesRecognitionCandidate(
                source_id=3,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        )

    current = (
        SalesRecognitionTarget(
            source_id=1,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("100.00"),
            tax_amount=Decimal("16.67"),
        ),
        SalesRecognitionTarget(
            source_id=2,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("100.00"),
            tax_amount=Decimal("16.66"),
        ),
        SalesRecognitionTarget(
            source_id=3,
            event_date=D1,
            quantity=Decimal("1"),
            gross_amount=Decimal("100.00"),
            tax_amount=Decimal("16.67"),
        ),
    )

    async def fake_events(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            SimpleNamespace(
                id=101,
                invoice_fulfillment_allocation_id=1,
                recognition_date=D1,
                recognized_quantity=Decimal("1"),
                recognized_gross_amount=Decimal("100.00"),
                recognized_tax_amount=Decimal("16.67"),
                currency_code="UAH",
                reversal_of_id=None,
            ),
            SimpleNamespace(
                id=102,
                invoice_fulfillment_allocation_id=2,
                recognition_date=D1,
                recognized_quantity=Decimal("1"),
                recognized_gross_amount=Decimal("100.00"),
                recognized_tax_amount=Decimal("16.66"),
                currency_code="UAH",
                reversal_of_id=None,
            ),
            SimpleNamespace(
                id=103,
                invoice_fulfillment_allocation_id=3,
                recognition_date=D1,
                recognized_quantity=Decimal("1"),
                recognized_gross_amount=Decimal("100.00"),
                recognized_tax_amount=Decimal("16.67"),
                currency_code="UAH",
                reversal_of_id=None,
            ),
        )

    calls = []

    async def fake_reconcile_source(
        db,
        *,
        company_id,
        target,
        currency_code,
        created_by,
        reversal_date,
    ):
        calls.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "_lock_sales_invoice_line_context",
        fake_context,
    )

    monkeypatch.setattr(
        service,
        "_load_active_sales_recognition_candidates",
        fake_candidates,
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_sales_recognition_events",
        fake_events,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_source",
        fake_reconcile_source,
    )

    result = (
        await service
        .reconcile_sales_recognition_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=100,
            invoice_line_id=200,
            adjustment_date=D2,
            created_by=1,
        )
    )

    assert current == result.current_targets

    assert [
        target.source_id
        for target in result.adjustments
    ] == [
        2,
        3,
    ]

    assert (
        result.adjustments[0].is_zero
    )

    assert [
        target.source_id
        for target in calls
    ] == [
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_reconciliation_empty_state_is_noop(
    monkeypatch,
):
    async def fake_context(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return (
            _document(),
            _line(
                tax_rate_code=None,
            ),
            None,
        )

    async def fake_candidates(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return ()

    async def fake_events(
        db,
        *,
        company_id,
        invoice_id,
        invoice_line_id,
    ):
        return ()

    async def should_not_run(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "source executor must not run"
        )

    monkeypatch.setattr(
        service,
        "_lock_sales_invoice_line_context",
        fake_context,
    )

    monkeypatch.setattr(
        service,
        "_load_active_sales_recognition_candidates",
        fake_candidates,
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_line_sales_recognition_events",
        fake_events,
    )

    monkeypatch.setattr(
        service,
        "reconcile_sales_recognition_source",
        should_not_run,
    )

    result = (
        await service
        .reconcile_sales_recognition_for_invoice_line(
            object(),
            company_id=1,
            invoice_id=100,
            invoice_line_id=200,
            adjustment_date=D2,
            created_by=1,
        )
    )

    assert result.current_targets == ()
    assert result.desired_targets == ()
    assert result.adjustments == ()
    assert result.created_events == ()
