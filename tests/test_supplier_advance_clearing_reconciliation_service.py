from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.supplier_advance_clearing_reconciliation_service as service

from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.supplier_advance_clearing_calculation_service import (
    SupplierAdvanceClearingTarget,
    SupplierAdvanceSettlementCandidate,
    SupplierEconomicLiabilityCandidate,
)
from app.services.supplier_economic_liability_calculation_service import (
    SupplierReceiptBaseAllocationTarget,
    SupplierVatLiabilityComponent,
)
from app.services.supplier_advance_clearing_reconciliation_service import (
    SupplierAdvanceClearingReconciliationDataIntegrityError,
    SupplierReceiptPeerSnapshot,
    build_supplier_advance_clearing_reconciliation_targets,
    build_supplier_receipt_base_targets_for_invoice,
    reconcile_supplier_advance_clearing_for_invoice,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)
D3 = date(2026, 9, 3)


def clearing_target(
    *,
    settlement_source_id: int = 10,
    liability_source_id: int = 20,
    event_date: date = D2,
    amount: str = "60.00",
):
    return SupplierAdvanceClearingTarget(
        settlement_source_id=settlement_source_id,
        liability_source_id=liability_source_id,
        event_date=event_date,
        amount=Decimal(amount),
        currency_code="UAH",
    )


def test_exact_reconciliation_target_is_omitted():
    current = clearing_target()

    result = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(current,),
            current_targets=(current,),
        )
    )

    assert result == ()


def test_missing_current_pair_becomes_zero_target():
    current = clearing_target()

    result = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(),
            current_targets=(current,),
        )
    )

    assert len(result) == 1
    assert result[0].amount == Decimal("0")
    assert result[0].event_date == current.event_date
    assert (
        result[0].settlement_source_id
        == current.settlement_source_id
    )
    assert (
        result[0].liability_source_id
        == current.liability_source_id
    )


def test_decrease_runs_before_increase():
    current = clearing_target(
        settlement_source_id=1,
        liability_source_id=10,
        amount="100.00",
    )

    decreased = clearing_target(
        settlement_source_id=1,
        liability_source_id=10,
        amount="40.00",
    )

    increased = clearing_target(
        settlement_source_id=2,
        liability_source_id=20,
        event_date=D3,
        amount="60.00",
    )

    result = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(
                increased,
                decreased,
            ),
            current_targets=(current,),
        )
    )

    assert result == (
        decreased,
        increased,
    )


def test_existing_pair_date_cannot_change():
    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="event_date changed",
    ):
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(
                clearing_target(
                    event_date=D3,
                ),
            ),
            current_targets=(
                clearing_target(
                    event_date=D2,
                ),
            ),
        )


def test_duplicate_desired_pair_fails_closed():
    target = clearing_target()

    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="Duplicate desired",
    ):
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(
                target,
                target,
            ),
            current_targets=(),
        )


def test_receipt_base_uses_all_active_peers_for_rounding():
    peers = (
        SupplierReceiptPeerSnapshot(
            source_id=1,
            invoice_id=100,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
        SupplierReceiptPeerSnapshot(
            source_id=2,
            invoice_id=200,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
        SupplierReceiptPeerSnapshot(
            source_id=3,
            invoice_id=100,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
    )

    result = (
        build_supplier_receipt_base_targets_for_invoice(
            peers=peers,
            invoice_source_ids=(1, 3),
            currency_code="UAH",
        )
    )

    assert tuple(
        (
            target.source_id,
            target.amount,
        )
        for target in result
    ) == (
        (1, Decimal("83.33")),
        (3, Decimal("83.33")),
    )


def test_receipt_base_missing_active_source_fails():
    peers = (
        SupplierReceiptPeerSnapshot(
            source_id=1,
            invoice_id=100,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("1"),
            receipt_price=Decimal("100"),
            allocation_quantity=Decimal("1"),
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="have no POSTED receipt base",
    ):
        build_supplier_receipt_base_targets_for_invoice(
            peers=peers,
            invoice_source_ids=(1, 2),
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_invoice_reconciliation_payment_first_partial_receipt(
    monkeypatch,
):
    db = SimpleNamespace()

    invoice = SimpleNamespace(
        id=100,
        company_id=1,
        direction=TradeDirection.PURCHASE,
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        currency_code="UAH",
    )

    open_item = SimpleNamespace(
        id=200,
        company_id=1,
        trade_document_id=100,
        item_type=CounterpartyOpenItemType.PAYABLE,
        currency_code="UAH",
        original_amount=Decimal("120.00"),
    )

    settlement = (
        SupplierAdvanceSettlementCandidate(
            source_id=10,
            event_date=D1,
            amount=Decimal("120.00"),
        )
    )

    liability = (
        SupplierEconomicLiabilityCandidate(
            source_id=20,
            event_date=D2,
            amount=Decimal("60.00"),
        )
    )

    allocation = SimpleNamespace(
        id=20,
        status="active",
    )

    monkeypatch.setattr(
        service,
        "_lock_purchase_invoice",
        AsyncMock(
            return_value=invoice
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_open_item",
        AsyncMock(
            return_value=open_item
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_settlement_candidates",
        AsyncMock(
            return_value=(settlement,)
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_fulfillment_allocations",
        AsyncMock(
            return_value=(allocation,)
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_economic_liability_candidates",
        AsyncMock(
            return_value=(liability,)
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_clearing_history",
        AsyncMock(
            return_value=()
        ),
    )

    persisted_event = SimpleNamespace(
        id=900,
        reversal_of_id=None,
    )

    persistence = AsyncMock(
        return_value=(persisted_event,)
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_source",
        persistence,
    )

    result = (
        await reconcile_supplier_advance_clearing_for_invoice(
            db,
            company_id=1,
            invoice_id=100,
            adjustment_date=D2,
            created_by=1,
        )
    )

    assert len(result.desired_targets) == 1

    desired = result.desired_targets[0]

    assert desired.amount == Decimal("60.00")
    assert desired.event_date == D2
    assert desired.settlement_source_id == 10
    assert desired.liability_source_id == 20
    assert (
        result.reconciliation_targets
        == result.desired_targets
    )
    assert result.created_events == (
        persisted_event,
    )

    persistence.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoice_reconciliation_no_receipt_creates_no_clearing(
    monkeypatch,
):
    db = SimpleNamespace()

    invoice = SimpleNamespace(
        id=100,
        company_id=1,
        direction=TradeDirection.PURCHASE,
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        currency_code="UAH",
    )

    open_item = SimpleNamespace(
        id=200,
        company_id=1,
        trade_document_id=100,
        item_type=CounterpartyOpenItemType.PAYABLE,
        currency_code="UAH",
        original_amount=Decimal("120.00"),
    )

    monkeypatch.setattr(
        service,
        "_lock_purchase_invoice",
        AsyncMock(
            return_value=invoice
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_open_item",
        AsyncMock(
            return_value=open_item
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_settlement_candidates",
        AsyncMock(
            return_value=(
                SupplierAdvanceSettlementCandidate(
                    source_id=10,
                    event_date=D1,
                    amount=Decimal("120.00"),
                ),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_invoice_fulfillment_allocations",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_economic_liability_candidates",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_clearing_history",
        AsyncMock(
            return_value=()
        ),
    )

    persistence = AsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_source",
        persistence,
    )

    result = (
        await reconcile_supplier_advance_clearing_for_invoice(
            db,
            company_id=1,
            invoice_id=100,
            adjustment_date=D1,
            created_by=1,
        )
    )

    assert result.desired_targets == ()
    assert result.reconciliation_targets == ()
    assert result.created_events == ()

    persistence.assert_not_awaited()

def test_purchase_return_base_reduces_supplier_liability():
    base = (
        SupplierReceiptBaseAllocationTarget(
            source_id=20,
            event_date=D2,
            amount=Decimal("100.00"),
            currency_code="UAH",
        ),
    )

    result = (
        service
        .apply_purchase_return_base_to_supplier_receipt_targets(
            base_targets=base,
            active_return_base_by_source={
                20: Decimal("40.00"),
            },
            currency_code="UAH",
        )
    )

    assert len(result) == 1
    assert result[0].source_id == 20
    assert result[0].event_date == D2
    assert result[0].amount == Decimal("60.00")
    assert result[0].currency_code == "UAH"


def test_purchase_return_full_base_can_reduce_receipt_base_to_zero():
    base = (
        SupplierReceiptBaseAllocationTarget(
            source_id=20,
            event_date=D2,
            amount=Decimal("100.00"),
            currency_code="UAH",
        ),
    )

    result = (
        service
        .apply_purchase_return_base_to_supplier_receipt_targets(
            base_targets=base,
            active_return_base_by_source={
                20: Decimal("100.00"),
            },
            currency_code="UAH",
        )
    )

    assert result[0].amount == Decimal("0.00")


def test_purchase_return_base_cannot_exceed_receipt_base():
    base = (
        SupplierReceiptBaseAllocationTarget(
            source_id=20,
            event_date=D2,
            amount=Decimal("100.00"),
            currency_code="UAH",
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="exceeds",
    ):
        (
            service
            .apply_purchase_return_base_to_supplier_receipt_targets(
                base_targets=base,
                active_return_base_by_source={
                    20: Decimal("100.01"),
                },
                currency_code="UAH",
            )
        )


def test_purchase_return_unknown_liability_source_fails_closed():
    base = (
        SupplierReceiptBaseAllocationTarget(
            source_id=20,
            event_date=D2,
            amount=Decimal("100.00"),
            currency_code="UAH",
        ),
    )

    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="no current receipt base",
    ):
        (
            service
            .apply_purchase_return_base_to_supplier_receipt_targets(
                base_targets=base,
                active_return_base_by_source={
                    21: Decimal("1.00"),
                },
                currency_code="UAH",
            )
        )


@pytest.mark.asyncio
async def test_economic_liability_subtracts_return_base_but_keeps_vat(
    monkeypatch,
):
    invoice = SimpleNamespace(
        company_id=1,
    )

    allocation = SimpleNamespace(
        id=20,
        status=(
            service
            .InvoiceFulfillmentAllocationStatus
            .ACTIVE
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_receipt_peer_snapshots",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "build_supplier_receipt_base_targets_for_invoice",
        lambda **kwargs: (
            SupplierReceiptBaseAllocationTarget(
                source_id=20,
                event_date=D2,
                amount=Decimal("100.00"),
                currency_code="UAH",
            ),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        AsyncMock(
            return_value={
                20: Decimal("40.00"),
            }
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        AsyncMock(
            return_value=(
                SupplierVatLiabilityComponent(
                    source_id=20,
                    event_date=D2,
                    amount=Decimal("20.00"),
                ),
            )
        ),
    )

    result = await (
        service
        ._load_supplier_economic_liability_candidates(
            object(),
            invoice=invoice,
            all_invoice_allocations=(
                allocation,
            ),
            currency_code="UAH",
        )
    )

    assert len(result) == 1

    # 100 receipt base
    # -40 ACTIVE Purchase Return base
    # +20 current INPUT VAT bridge
    # =80 current supplier liability.
    assert result[0].source_id == 20
    assert result[0].event_date == D2
    assert result[0].amount == Decimal("80.00")


@pytest.mark.asyncio
async def test_full_base_return_keeps_current_vat_liability(
    monkeypatch,
):
    invoice = SimpleNamespace(
        company_id=1,
    )

    allocation = SimpleNamespace(
        id=20,
        status=(
            service
            .InvoiceFulfillmentAllocationStatus
            .ACTIVE
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_receipt_peer_snapshots",
        AsyncMock(
            return_value=()
        ),
    )

    monkeypatch.setattr(
        service,
        "build_supplier_receipt_base_targets_for_invoice",
        lambda **kwargs: (
            SupplierReceiptBaseAllocationTarget(
                source_id=20,
                event_date=D2,
                amount=Decimal("100.00"),
                currency_code="UAH",
            ),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        AsyncMock(
            return_value={
                20: Decimal("100.00"),
            }
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        AsyncMock(
            return_value=(
                SupplierVatLiabilityComponent(
                    source_id=20,
                    event_date=D2,
                    amount=Decimal("20.00"),
                ),
            )
        ),
    )

    result = await (
        service
        ._load_supplier_economic_liability_candidates(
            object(),
            invoice=invoice,
            all_invoice_allocations=(
                allocation,
            ),
            currency_code="UAH",
        )
    )

    assert len(result) == 1

    # Purchase Return accounting has removed only receipt base.
    # VAT/RK correction has NOT happened yet.
    assert result[0].amount == Decimal("20.00")
