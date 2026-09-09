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


def test_existing_pair_accounting_date_cannot_precede_source_floor():
    with pytest.raises(
        SupplierAdvanceClearingReconciliationDataIntegrityError,
        match=(
            "current accounting date precedes desired "
            "source-eligible event_date"
        ),
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

    async def no_active_pvc_vat_overlay(
        db,
        *,
        company_id,
        vat_components,
        all_invoice_allocations,
        active_invoice_source_ids,
        currency_code,
    ):
        return vat_components

    monkeypatch.setattr(
        service,
        "_apply_purchase_value_correction_vat_to_supplier_components",
        no_active_pvc_vat_overlay,
    )

    async def no_active_purchase_return_vat(
        *args,
        **kwargs,
    ):
        return {}

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        no_active_purchase_return_vat,
    )

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

    async def no_active_purchase_value_correction(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        no_active_purchase_value_correction,
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

    async def no_active_pvc_vat_overlay(
        db,
        *,
        company_id,
        vat_components,
        all_invoice_allocations,
        active_invoice_source_ids,
        currency_code,
    ):
        return vat_components

    monkeypatch.setattr(
        service,
        "_apply_purchase_value_correction_vat_to_supplier_components",
        no_active_pvc_vat_overlay,
    )

    async def no_active_purchase_return_vat(
        *args,
        **kwargs,
    ):
        return {}

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        no_active_purchase_return_vat,
    )

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

    async def no_active_purchase_value_correction(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        no_active_purchase_value_correction,
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



def test_active_purchase_return_vat_reduces_supplier_vat_component():
    from datetime import date
    from decimal import Decimal

    components = (
        service.SupplierVatLiabilityComponent(
            source_id=20,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount=Decimal(
                "20.00"
            ),
        ),
    )

    result = (
        service.apply_purchase_return_vat_to_supplier_vat_components(
            vat_components=components,
            active_return_vat_by_source={
                20:
                Decimal(
                    "5.00"
                ),
            },
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[
            0
        ].source_id
        == 20
    )

    assert (
        result[
            0
        ].amount
        == Decimal(
            "15.00"
        )
    )


def test_full_purchase_return_vat_reduction_removes_vat_component():
    from datetime import date
    from decimal import Decimal

    components = (
        service.SupplierVatLiabilityComponent(
            source_id=20,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount=Decimal(
                "20.00"
            ),
        ),
    )

    result = (
        service.apply_purchase_return_vat_to_supplier_vat_components(
            vat_components=components,
            active_return_vat_by_source={
                20:
                Decimal(
                    "20.00"
                ),
            },
            currency_code="UAH",
        )
    )

    assert result == ()


def test_purchase_return_vat_cannot_exceed_current_vat_liability():
    from datetime import date
    from decimal import Decimal

    components = (
        service.SupplierVatLiabilityComponent(
            source_id=20,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount=Decimal(
                "20.00"
            ),
        ),
    )

    with pytest.raises(
        service.SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="exceeds current INPUT VAT",
    ):
        service.apply_purchase_return_vat_to_supplier_vat_components(
            vat_components=components,
            active_return_vat_by_source={
                20:
                Decimal(
                    "20.01"
                ),
            },
            currency_code="UAH",
        )


def test_purchase_return_vat_unknown_supplier_source_fails_closed():
    from datetime import date
    from decimal import Decimal

    components = (
        service.SupplierVatLiabilityComponent(
            source_id=20,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount=Decimal(
                "20.00"
            ),
        ),
    )

    with pytest.raises(
        service.SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="no current INPUT VAT liability",
    ):
        service.apply_purchase_return_vat_to_supplier_vat_components(
            vat_components=components,
            active_return_vat_by_source={
                99:
                Decimal(
                    "1.00"
                ),
            },
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_active_purchase_return_vat_loader_resolves_immutable_history():
    from decimal import Decimal
    from types import SimpleNamespace

    class RowsResult:
        def __init__(
            self,
            rows,
        ):
            self.rows = rows

        def all(
            self,
        ):
            return self.rows

    class Scalars:
        def __init__(
            self,
            rows,
        ):
            self.rows = rows

        def all(
            self,
        ):
            return self.rows

    class EventsResult:
        def __init__(
            self,
            rows,
        ):
            self.rows = rows

        def scalars(
            self,
        ):
            return Scalars(
                self.rows
            )

    class DB:
        def __init__(
            self,
        ):
            self.calls = 0

        async def execute(
            self,
            statement,
        ):
            self.calls += 1

            if self.calls == 1:
                return RowsResult(
                    (
                        (
                            100,
                            20,
                        ),
                        (
                            101,
                            20,
                        ),
                    )
                )

            if self.calls == 2:
                return EventsResult(
                    (
                        SimpleNamespace(
                            id=1,
                            purchase_return_recognition_event_id=100,
                            tax_calculation_id=70,
                            basis_kind=(
                                "goods_received_by_supplier"
                            ),
                            adjusted_tax_amount=Decimal(
                                "20.00"
                            ),
                            currency_code="UAH",
                            reversal_of_id=None,
                        ),
                        SimpleNamespace(
                            id=2,
                            purchase_return_recognition_event_id=100,
                            tax_calculation_id=70,
                            basis_kind=(
                                "goods_received_by_supplier"
                            ),
                            adjusted_tax_amount=Decimal(
                                "20.00"
                            ),
                            currency_code="UAH",
                            reversal_of_id=1,
                        ),
                        SimpleNamespace(
                            id=3,
                            purchase_return_recognition_event_id=101,
                            tax_calculation_id=71,
                            basis_kind=(
                                "refund_by_supplier"
                            ),
                            adjusted_tax_amount=Decimal(
                                "5.00"
                            ),
                            currency_code="UAH",
                            reversal_of_id=None,
                        ),
                    )
                )

            raise AssertionError(
                "Unexpected DB execute"
            )

    result = (
        await service._load_active_purchase_return_vat_by_source(
            DB(),
            company_id=1,
            active_source_ids={
                20
            },
            currency_code="UAH",
        )
    )

    assert result == {
        20:
        Decimal(
            "5.00"
        )
    }


@pytest.mark.asyncio
async def test_supplier_liability_loader_applies_active_prvat_reduction(
    monkeypatch,
):

    async def no_active_pvc_vat_overlay(
        db,
        *,
        company_id,
        vat_components,
        all_invoice_allocations,
        active_invoice_source_ids,
        currency_code,
    ):
        return vat_components

    monkeypatch.setattr(
        service,
        "_apply_purchase_value_correction_vat_to_supplier_components",
        no_active_pvc_vat_overlay,
    )

    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    receipt_date = date(
        2026,
        9,
        1,
    )

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

    async def load_peers(
        *args,
        **kwargs,
    ):
        return ()

    def build_base(
        *,
        peers,
        invoice_source_ids,
        currency_code,
    ):
        assert peers == ()

        assert invoice_source_ids == (
            20,
        )

        assert currency_code == "UAH"

        return (
            service.SupplierReceiptBaseAllocationTarget(
                source_id=20,
                event_date=receipt_date,
                amount=Decimal(
                    "100.00"
                ),
                currency_code="UAH",
            ),
        )

    async def load_return_base(
        *args,
        **kwargs,
    ):
        return {
            20:
            Decimal(
                "40.00"
            ),
        }

    async def load_vat(
        *args,
        **kwargs,
    ):
        return (
            service.SupplierVatLiabilityComponent(
                source_id=20,
                event_date=receipt_date,
                amount=Decimal(
                    "20.00"
                ),
            ),
        )

    async def load_return_vat(
        *args,
        **kwargs,
    ):
        return {
            20:
            Decimal(
                "5.00"
            ),
        }

    monkeypatch.setattr(
        service,
        "_load_receipt_peer_snapshots",
        load_peers,
    )

    monkeypatch.setattr(
        service,
        "build_supplier_receipt_base_targets_for_invoice",
        build_base,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        load_return_base,
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        load_vat,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        load_return_vat,
    )

    async def no_active_purchase_value_correction(
        *args,
        **kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        no_active_purchase_value_correction,
    )

    result = (
        await service._load_supplier_economic_liability_candidates(
            object(),
            invoice=invoice,
            all_invoice_allocations=(
                allocation,
            ),
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    candidate = result[
        0
    ]

    assert candidate.source_id == 20

    assert (
        candidate.event_date
        == receipt_date
    )

    assert (
        candidate.amount
        == Decimal(
            "75.00"
        )
    )


PVC631_D1 = date(
    2026,
    9,
    1,
)

PVC631_D2 = date(
    2026,
    9,
    2,
)

PVC631_D3 = date(
    2026,
    9,
    3,
)


def _pvc631_event(
    *,
    event_id,
    correction_id=100,
    source_id=10,
    recognition_date=PVC631_D2,
    original="100.00",
    corrected="90.00",
    reversal_of_id=None,
):
    event = (
        service
        .PurchaseValueCorrectionAllocationEvent(
            company_id=1,
            trade_value_correction_event_id=(
                correction_id
            ),
            invoice_fulfillment_allocation_id=(
                source_id
            ),
            recognition_date=(
                recognition_date
            ),
            original_allocated_base_amount=Decimal(
                original
            ),
            corrected_allocated_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=(
                reversal_of_id
            ),
        )
    )

    event.id = event_id

    return event


def test_pvc631_active_adjustment_decrease():
    result = (
        service
        ._build_active_purchase_value_correction_base_adjustments(
            events=(
                _pvc631_event(
                    event_id=1,
                ),
            ),
            active_source_ids={
                10,
            },
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    assert result[0].source_id == 10
    assert (
        result[0].recognition_date
        == PVC631_D2
    )
    assert (
        result[0].base_delta
        == Decimal(
            "-10.00"
        )
    )


def test_pvc631_reversed_original_contributes_nothing():
    original = _pvc631_event(
        event_id=1,
    )

    reversal = _pvc631_event(
        event_id=2,
        recognition_date=PVC631_D3,
        reversal_of_id=1,
    )

    result = (
        service
        ._build_active_purchase_value_correction_base_adjustments(
            events=(
                original,
                reversal,
            ),
            active_source_ids={
                10,
            },
            currency_code="UAH",
        )
    )

    assert result == ()


def test_pvc631_replacement_after_reversal_is_active():
    original = _pvc631_event(
        event_id=1,
        corrected="90.00",
    )

    reversal = _pvc631_event(
        event_id=2,
        corrected="90.00",
        recognition_date=PVC631_D3,
        reversal_of_id=1,
    )

    replacement = _pvc631_event(
        event_id=3,
        recognition_date=PVC631_D3,
        corrected="95.00",
    )

    result = (
        service
        ._build_active_purchase_value_correction_base_adjustments(
            events=(
                original,
                reversal,
                replacement,
            ),
            active_source_ids={
                10,
            },
            currency_code="UAH",
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].base_delta
        == Decimal(
            "-5.00"
        )
    )


def test_pvc631_multiple_independent_corrections_same_ifa_remain_distinct():
    first = _pvc631_event(
        event_id=1,
        correction_id=100,
        original="100.00",
        corrected="90.00",
    )

    second = _pvc631_event(
        event_id=2,
        correction_id=101,
        recognition_date=PVC631_D3,
        original="90.00",
        corrected="80.00",
    )

    result = (
        service
        ._build_active_purchase_value_correction_base_adjustments(
            events=(
                first,
                second,
            ),
            active_source_ids={
                10,
            },
            currency_code="UAH",
        )
    )

    assert tuple(
        item.base_delta
        for item in result
    ) == (
        Decimal("-10.00"),
        Decimal("-10.00"),
    )


def test_pvc631_reversal_changed_snapshot_fails_closed():
    original = _pvc631_event(
        event_id=1,
    )

    bad_reversal = _pvc631_event(
        event_id=2,
        recognition_date=PVC631_D3,
        corrected="80.00",
        reversal_of_id=1,
    )

    with pytest.raises(
        service
        .SupplierAdvanceClearingReconciliationDataIntegrityError,
        match="immutable monetary state",
    ):
        (
            service
            ._build_active_purchase_value_correction_base_adjustments(
                events=(
                    original,
                    bad_reversal,
                ),
                active_source_ids={
                    10,
                },
                currency_code="UAH",
            )
        )


@pytest.mark.asyncio
async def test_pvc631_empty_active_sources_skip_database():
    class ForbiddenDb:
        async def execute(
            self,
            statement,
        ):
            raise AssertionError(
                "DB must not be queried"
            )

    result = (
        await service
        ._load_active_purchase_value_correction_base_adjustments(
            ForbiddenDb(),
            company_id=1,
            active_source_ids=set(),
            currency_code="UAH",
        )
    )

    assert result == ()


@pytest.mark.asyncio
async def test_pvc631_supplier_liability_loader_wires_adjustments(
    monkeypatch,
):
    class Obj:
        def __init__(
            self,
            **values,
        ):
            for key, value in values.items():
                setattr(
                    self,
                    key,
                    value,
                )

    invoice = Obj(
        company_id=1,
    )

    allocation = Obj(
        id=10,
        status=(
            service
            .InvoiceFulfillmentAllocationStatus
            .ACTIVE
        ),
    )

    async def peers(
        db,
        *,
        company_id,
        active_invoice_allocations,
    ):
        return (
            "peer",
        )

    def base_targets(
        *,
        peers,
        invoice_source_ids,
        currency_code,
    ):
        return (
            service
            .SupplierReceiptBaseAllocationTarget(
                source_id=10,
                event_date=PVC631_D1,
                amount=Decimal(
                    "100.00"
                ),
                currency_code="UAH",
            ),
        )

    async def return_base(
        db,
        *,
        company_id,
        active_source_ids,
        currency_code,
    ):
        return {}

    def apply_return_base(
        *,
        base_targets,
        active_return_base_by_source,
        currency_code,
    ):
        return base_targets

    async def vat_components(
        db,
        *,
        company_id,
        all_invoice_allocations,
        active_invoice_source_ids,
        currency_code,
    ):
        return ()

    async def return_vat(
        db,
        *,
        company_id,
        active_source_ids,
        currency_code,
    ):
        return {}

    def apply_return_vat(
        *,
        vat_components,
        active_return_vat_by_source,
        currency_code,
    ):
        return vat_components

    expected_adjustment = (
        service
        .SupplierEconomicLiabilityBaseAdjustment(
            source_id=10,
            recognition_date=PVC631_D2,
            base_delta=Decimal(
                "-10.00"
            ),
            currency_code="UAH",
        )
    )

    async def pvc_adjustments(
        db,
        *,
        company_id,
        active_source_ids,
        currency_code,
    ):
        assert active_source_ids == {
            10,
        }

        return (
            expected_adjustment,
        )

    captured = {}

    def adjusted_builder(
        *,
        base_targets,
        vat_components,
        base_adjustments,
        currency_code,
    ):
        captured[
            "base_targets"
        ] = base_targets

        captured[
            "vat_components"
        ] = vat_components

        captured[
            "base_adjustments"
        ] = base_adjustments

        captured[
            "currency_code"
        ] = currency_code

        return ()

    monkeypatch.setattr(
        service,
        "_load_receipt_peer_snapshots",
        peers,
    )

    monkeypatch.setattr(
        service,
        "build_supplier_receipt_base_targets_for_invoice",
        base_targets,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        return_base,
    )

    monkeypatch.setattr(
        service,
        "apply_purchase_return_base_to_supplier_receipt_targets",
        apply_return_base,
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        vat_components,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        return_vat,
    )

    monkeypatch.setattr(
        service,
        "apply_purchase_return_vat_to_supplier_vat_components",
        apply_return_vat,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        pvc_adjustments,
    )

    monkeypatch.setattr(
        service,
        "build_supplier_economic_liability_candidates_with_base_adjustments",
        adjusted_builder,
    )

    result = (
        await service
        ._load_supplier_economic_liability_candidates(
            object(),
            invoice=invoice,
            all_invoice_allocations=(
                allocation,
            ),
            currency_code="UAH",
        )
    )

    assert result == ()

    assert (
        captured[
            "base_adjustments"
        ]
        == (
            expected_adjustment,
        )
    )

    assert (
        captured[
            "currency_code"
        ]
        == "UAH"
    )


def test_later_current_accounting_date_allows_earlier_source_floor_noop():
    current = clearing_target(
        event_date=D3,
        amount="60.00",
    )

    desired = clearing_target(
        event_date=D2,
        amount="60.00",
    )

    result = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert result == ()


def test_later_current_accounting_date_allows_changed_source_floor_target():
    current = clearing_target(
        event_date=D3,
        amount="60.00",
    )

    desired = clearing_target(
        event_date=D2,
        amount="40.00",
    )

    result = (
        build_supplier_advance_clearing_reconciliation_targets(
            desired_targets=(
                desired,
            ),
            current_targets=(
                current,
            ),
        )
    )

    assert result == (
        desired,
    )
