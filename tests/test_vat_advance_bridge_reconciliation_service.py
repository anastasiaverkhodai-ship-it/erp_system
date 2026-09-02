from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.vat_advance_bridge_reconciliation_service as service

from app.services.sales_recognition_calculation_service import (
    SalesRecognitionTarget,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidateKind,
    TaxRecognitionSourceTarget,
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
    8,
    31,
)

D2 = date(
    2026,
    9,
    1,
)

D3 = date(
    2026,
    9,
    2,
)


def _sales(
    *,
    source_id=10,
    event_date=D1,
    tax_amount="20.00",
):
    return SalesRecognitionTarget(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal("1.0000"),
        gross_amount=Decimal("120.00"),
        tax_amount=Decimal(
            tax_amount
        ),
    )


def _tax(
    *,
    source_id=10,
    kind=(
        TaxRecognitionCandidateKind
        .FULFILLMENT
    ),
    event_date=D1,
    tax_amount="20.00",
):
    return TaxRecognitionSourceTarget(
        kind=kind,
        source_id=source_id,
        event_date=event_date,
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal(
            tax_amount
        ),
    )


def _bridge(
    *,
    source_id=10,
    tax_calculation_id=20,
    event_date=D1,
    amount="20.00",
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


def _build(
    *,
    sales=(),
    tax=(),
    bridge=(),
    tax_calculation_id=20,
    adjustment_date=D3,
):
    return (
        service
        .build_vat_advance_bridge_reconciliation_targets(
            tax_calculation_id=(
                tax_calculation_id
            ),
            sales_targets=sales,
            tax_targets=tax,
            current_bridge_targets=bridge,
            adjustment_date=(
                adjustment_date
            ),
            currency_code="UAH",
        )
    )


def test_payment_first_builds_full_bridge():
    targets = _build(
        sales=(
            _sales(),
        ),
    )

    assert len(targets) == 1

    assert (
        targets[0].amount
        == Decimal("20.00")
    )

    assert targets[0].event_date == D1


def test_fulfillment_first_builds_zero_bridge():
    targets = _build(
        sales=(
            _sales(),
        ),
        tax=(
            _tax(),
        ),
    )

    assert len(targets) == 1
    assert targets[0].is_zero


def test_partial_prepayment_builds_partial_bridge():
    targets = _build(
        sales=(
            _sales(),
        ),
        tax=(
            _tax(
                tax_amount="10.00",
            ),
        ),
    )

    assert (
        targets[0].amount
        == Decimal("10.00")
    )


def test_settlement_tax_does_not_reduce_bridge():
    targets = _build(
        sales=(
            _sales(),
        ),
        tax=(
            _tax(
                kind=(
                    TaxRecognitionCandidateKind
                    .SETTLEMENT
                ),
                tax_amount="20.00",
            ),
        ),
    )

    assert (
        targets[0].amount
        == Decimal("20.00")
    )


def test_cash_method_fulfillment_keeps_full_bridge():
    targets = _build(
        sales=(
            _sales(),
        ),
        tax=(),
    )

    assert (
        targets[0].amount
        == Decimal("20.00")
    )


def test_tax_migration_reduces_existing_bridge_to_zero():
    targets = _build(
        sales=(
            _sales(),
        ),
        tax=(
            _tax(
                tax_amount="20.00",
            ),
        ),
        bridge=(
            _bridge(
                amount="20.00",
            ),
        ),
    )

    assert len(targets) == 1
    assert targets[0].is_zero
    assert targets[0].event_date == D1


def test_removed_sales_reverses_existing_bridge():
    targets = _build(
        sales=(),
        tax=(),
        bridge=(
            _bridge(
                event_date=D1,
                amount="20.00",
            ),
        ),
        adjustment_date=D3,
    )

    assert len(targets) == 1
    assert targets[0].is_zero

    # Zero target preserves immutable economic
    # source date, not adjustment date.
    assert targets[0].event_date == D1


def test_fulfillment_tax_above_sales_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=(
            "Fulfillment-source VAT amount "
            "cannot exceed Sales recognition VAT amount"
        ),
    ):
        _build(
            sales=(
                _sales(
                    tax_amount="10.00",
                ),
            ),
            tax=(
                _tax(
                    tax_amount="20.00",
                ),
            ),
        )


def test_tax_without_sales_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
    ):
        _build(
            tax=(
                _tax(
                    tax_amount="20.00",
                ),
            ),
        )


def test_duplicate_sales_source_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="duplicate source",
    ):
        _build(
            sales=(
                _sales(),
                _sales(),
            ),
        )


def test_duplicate_fulfillment_tax_source_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=(
            "duplicate fulfillment source"
        ),
    ):
        _build(
            sales=(
                _sales(),
            ),
            tax=(
                _tax(
                    tax_amount="5.00",
                ),
                _tax(
                    tax_amount="5.00",
                ),
            ),
        )


def test_duplicate_current_bridge_source_fails_closed():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="duplicate source",
    ):
        _build(
            bridge=(
                _bridge(),
                _bridge(),
            ),
        )


def test_current_bridge_tax_calculation_cannot_change():
    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match="TaxCalculation changed",
    ):
        _build(
            bridge=(
                _bridge(
                    tax_calculation_id=999,
                ),
            ),
        )


def test_multiple_sources_are_sorted_by_event_date_then_source():
    targets = _build(
        sales=(
            _sales(
                source_id=30,
                event_date=D2,
                tax_amount="3.00",
            ),
            _sales(
                source_id=20,
                event_date=D1,
                tax_amount="2.00",
            ),
            _sales(
                source_id=10,
                event_date=D1,
                tax_amount="1.00",
            ),
        ),
    )

    assert [
        target.source_id
        for target in targets
    ] == [
        10,
        20,
        30,
    ]


@pytest.mark.parametrize(
    "tax_calculation_id, adjustment_date, currency_code",
    [
        (
            0,
            D1,
            "UAH",
        ),
        (
            -1,
            D1,
            "UAH",
        ),
        (
            20,
            "2026-08-31",
            "UAH",
        ),
        (
            20,
            D1,
            "UA",
        ),
    ],
)
def test_invalid_build_context_is_rejected(
    tax_calculation_id,
    adjustment_date,
    currency_code,
):
    kwargs = dict(
        tax_calculation_id=(
            tax_calculation_id
        ),
        sales_targets=(),
        tax_targets=(),
        current_bridge_targets=(),
        adjustment_date=(
            adjustment_date
        ),
        currency_code=(
            currency_code
        ),
    )

    with pytest.raises(
        (
            ValueError,
            VatAdvanceBridgeDataIntegrityError,
        )
    ):
        service.build_vat_advance_bridge_reconciliation_targets(
            **kwargs
        )


def test_validate_tax_calculation_accepts_output_vat():
    calculation = SimpleNamespace(
        id=20,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        currency_code="UAH",
        trade_document_id=100,
        trade_document_line_id=101,
    )

    service._validate_tax_calculation(
        calculation,
        company_id=1,
        tax_calculation_id=20,
    )


@pytest.mark.parametrize(
    "overrides, match",
    [
        (
            {
                "company_id": 2,
            },
            "company mismatch",
        ),
        (
            {
                "id": 21,
            },
            "identity mismatch",
        ),
        (
            {
                "tax_type": "income_tax",
            },
            "unsupported tax type",
        ),
        (
            {
                "direction": TaxDirection.INPUT,
            },
            "OUTPUT VAT",
        ),
        (
            {
                "currency_code": "UA",
            },
            "currency",
        ),
        (
            {
                "trade_document_line_id": None,
            },
            "valid Invoice line",
        ),
    ],
)
def test_validate_tax_calculation_fails_closed(
    overrides,
    match,
):
    values = dict(
        id=20,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        currency_code="UAH",
        trade_document_id=100,
        trade_document_line_id=101,
    )

    values.update(
        overrides
    )

    calculation = SimpleNamespace(
        **values
    )

    with pytest.raises(
        VatAdvanceBridgeDataIntegrityError,
        match=match,
    ):
        service._validate_tax_calculation(
            calculation,
            company_id=1,
            tax_calculation_id=20,
        )


@pytest.mark.asyncio
async def test_reconcile_coordinates_current_states_and_persistence(
    monkeypatch,
):
    calculation = SimpleNamespace(
        id=20,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        currency_code="UAH",
        trade_document_id=100,
        trade_document_line_id=101,
    )

    sales_targets = (
        _sales(
            source_id=10,
            tax_amount="20.00",
        ),
        _sales(
            source_id=11,
            tax_amount="20.00",
        ),
    )

    tax_targets = (
        _tax(
            source_id=10,
            tax_amount="20.00",
        ),
        _tax(
            source_id=11,
            tax_amount="10.00",
        ),
    )

    bridge_targets = (
        _bridge(
            source_id=10,
            amount="20.00",
        ),
    )

    async def fake_load_calculation(
        db,
        *,
        company_id,
        tax_calculation_id,
    ):
        assert company_id == 1
        assert tax_calculation_id == 20
        return calculation

    async def fake_load_sales(
        db,
        *,
        calculation,
    ):
        return (
            SimpleNamespace(),
        )

    async def fake_load_tax(
        db,
        *,
        calculation,
    ):
        return (
            SimpleNamespace(),
        )

    async def fake_load_bridge(
        db,
        *,
        calculation,
    ):
        return (
            SimpleNamespace(),
        )

    monkeypatch.setattr(
        service,
        "_load_tax_calculation",
        fake_load_calculation,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_recognition_events",
        fake_load_sales,
    )

    monkeypatch.setattr(
        service,
        "_load_tax_recognition_events",
        fake_load_tax,
    )

    monkeypatch.setattr(
        service,
        "_load_bridge_events",
        fake_load_bridge,
    )

    monkeypatch.setattr(
        service,
        "build_current_sales_recognition_targets",
        lambda **kwargs: (
            sales_targets
        ),
    )

    monkeypatch.setattr(
        service,
        "build_current_output_tax_recognition_targets",
        lambda events: (
            tax_targets
        ),
    )

    monkeypatch.setattr(
        service,
        "build_current_vat_advance_bridge_targets",
        lambda **kwargs: (
            bridge_targets
        ),
    )

    calls = []

    async def fake_reconcile(
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
                target.source_id,
                target.amount,
                reversal_date,
            )
        )

        if target.is_zero:
            return (
                SimpleNamespace(
                    id=100 + target.source_id,
                ),
            )

        return (
            SimpleNamespace(
                id=200 + target.source_id,
            ),
        )

    monkeypatch.setattr(
        service,
        "reconcile_vat_advance_bridge_source",
        fake_reconcile,
    )

    result = (
        await service
        .reconcile_vat_advance_bridge_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D3,
            created_by=2,
        )
    )

    assert result.tax_calculation_id == 20

    assert [
        (
            target.source_id,
            target.amount,
        )
        for target in result.desired_targets
    ] == [
        (
            10,
            Decimal("0.00"),
        ),
        (
            11,
            Decimal("10.00"),
        ),
    ]

    assert calls == [
        (
            10,
            Decimal("0.00"),
            D3,
        ),
        (
            11,
            Decimal("10.00"),
            D3,
        ),
    ]

    assert len(
        result.created_events
    ) == 2


@pytest.mark.asyncio
async def test_reconcile_no_sources_is_clean_noop(
    monkeypatch,
):
    calculation = SimpleNamespace(
        id=20,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.OUTPUT,
        currency_code="UAH",
        trade_document_id=100,
        trade_document_line_id=101,
    )

    async def fake_load_calculation(
        *args,
        **kwargs,
    ):
        return calculation

    async def empty_loader(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_tax_calculation",
        fake_load_calculation,
    )

    monkeypatch.setattr(
        service,
        "_load_sales_recognition_events",
        empty_loader,
    )

    monkeypatch.setattr(
        service,
        "_load_tax_recognition_events",
        empty_loader,
    )

    monkeypatch.setattr(
        service,
        "_load_bridge_events",
        empty_loader,
    )

    result = (
        await service
        .reconcile_vat_advance_bridge_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D3,
            created_by=2,
        )
    )

    assert result.desired_targets == ()
    assert result.created_events == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_id, tax_calculation_id, adjustment_date, created_by",
    [
        (
            0,
            20,
            D1,
            1,
        ),
        (
            1,
            0,
            D1,
            1,
        ),
        (
            1,
            20,
            "2026-08-31",
            1,
        ),
        (
            1,
            20,
            D1,
            0,
        ),
    ],
)
async def test_reconcile_validates_context_before_db(
    company_id,
    tax_calculation_id,
    adjustment_date,
    created_by,
):
    with pytest.raises(
        ValueError
    ):
        await (
            service
            .reconcile_vat_advance_bridge_for_tax_calculation(
                object(),
                company_id=company_id,
                tax_calculation_id=(
                    tax_calculation_id
                ),
                adjustment_date=(
                    adjustment_date
                ),
                created_by=created_by,
            )
        )
