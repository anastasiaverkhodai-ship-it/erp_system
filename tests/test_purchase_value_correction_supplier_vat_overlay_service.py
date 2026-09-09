from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.supplier_advance_clearing_reconciliation_service as service

from app.models.purchase_value_correction_vat_adjustment_event import (
    PurchaseValueCorrectionVatAdjustmentEvent,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.supplier_economic_liability_calculation_service import (
    SupplierReceiptBaseAllocationTarget,
    SupplierVatLiabilityComponent,
)


D1 = date(2026, 9, 1)
D5 = date(2026, 9, 5)
D6 = date(2026, 9, 6)


def pvc_vat_event(
    *,
    event_id: int,
    correction_id: int = 100,
    tax_calculation_id: int = 200,
    adjustment_date: date = D5,
    adjustment_kind: str = "decrease",
    taxable_base: str = "10.00",
    tax_amount: str = "2.00",
    reversal_of_id: int | None = None,
):
    event = PurchaseValueCorrectionVatAdjustmentEvent(
        company_id=1,
        trade_value_correction_event_id=correction_id,
        tax_calculation_id=tax_calculation_id,
        adjustment_date=adjustment_date,
        adjustment_kind=adjustment_kind,
        adjusted_taxable_base=Decimal(taxable_base),
        adjusted_tax_amount=Decimal(tax_amount),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=reversal_of_id,
    )

    # Unit-level immutable-history tests require persistent identity,
    # but do not perform DB persistence.
    event.id = event_id

    return event


def test_active_original_contributes():
    original = pvc_vat_event(
        event_id=1,
    )

    result = (
        service
        ._build_active_purchase_value_correction_vat_events(
            events=(original,),
            currency_code="UAH",
        )
    )

    assert result == (
        original,
    )


def test_reversed_original_does_not_contribute():
    original = pvc_vat_event(
        event_id=1,
    )

    reversal = pvc_vat_event(
        event_id=2,
        adjustment_date=D6,
        reversal_of_id=1,
    )

    result = (
        service
        ._build_active_purchase_value_correction_vat_events(
            events=(
                original,
                reversal,
            ),
            currency_code="UAH",
        )
    )

    assert result == ()


def test_reversal_cannot_precede_original():
    original = pvc_vat_event(
        event_id=1,
        adjustment_date=D6,
    )

    reversal = pvc_vat_event(
        event_id=2,
        adjustment_date=D5,
        reversal_of_id=1,
    )

    with pytest.raises(
        service
        .SupplierAdvanceClearingReconciliationDataIntegrityError
    ):
        (
            service
            ._build_active_purchase_value_correction_vat_events(
                events=(
                    original,
                    reversal,
                ),
                currency_code="UAH",
            )
        )


def test_reversal_cannot_change_tax_amount():
    original = pvc_vat_event(
        event_id=1,
        tax_amount="2.00",
    )

    reversal = pvc_vat_event(
        event_id=2,
        adjustment_date=D6,
        tax_amount="2.01",
        reversal_of_id=1,
    )

    with pytest.raises(
        service
        .SupplierAdvanceClearingReconciliationDataIntegrityError
    ):
        (
            service
            ._build_active_purchase_value_correction_vat_events(
                events=(
                    original,
                    reversal,
                ),
                currency_code="UAH",
            )
        )


def test_reversal_cannot_change_direction():
    original = pvc_vat_event(
        event_id=1,
        adjustment_kind="decrease",
    )

    reversal = pvc_vat_event(
        event_id=2,
        adjustment_date=D6,
        adjustment_kind="increase",
        reversal_of_id=1,
    )

    with pytest.raises(
        service
        .SupplierAdvanceClearingReconciliationDataIntegrityError
    ):
        (
            service
            ._build_active_purchase_value_correction_vat_events(
                events=(
                    original,
                    reversal,
                ),
                currency_code="UAH",
            )
        )


def test_original_can_be_reversed_and_replaced():
    original = pvc_vat_event(
        event_id=1,
        tax_amount="2.00",
    )

    reversal = pvc_vat_event(
        event_id=2,
        adjustment_date=D6,
        tax_amount="2.00",
        reversal_of_id=1,
    )

    replacement = pvc_vat_event(
        event_id=3,
        adjustment_date=D6,
        tax_amount="3.00",
    )

    result = (
        service
        ._build_active_purchase_value_correction_vat_events(
            events=(
                original,
                reversal,
                replacement,
            ),
            currency_code="UAH",
        )
    )

    assert result == (
        replacement,
    )


@pytest.mark.asyncio
async def test_supplier_liability_orchestration_applies_pvc_vat_overlay(
    monkeypatch,
):
    """
    Ordinary current supplier liability:

        receipt base = 100
        economic INPUT VAT = 20

    PVC economic VAT decrease:
        20 -> 18

    Expected supplier 631 capacity:
        100 + 18 = 118.

    This test does NOT test DB loading. It proves the production
    orchestration consumes the output of the PVC VAT overlay before
    building final supplier economic liability.
    """

    invoice = SimpleNamespace(
        company_id=1,
    )

    allocation = SimpleNamespace(
        id=20,
        status=(
            InvoiceFulfillmentAllocationStatus.ACTIVE
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
                event_date=D1,
                amount=Decimal("100.00"),
                currency_code="UAH",
            ),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        AsyncMock(
            return_value={}
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        AsyncMock(
            return_value=(
                SupplierVatLiabilityComponent(
                    source_id=20,
                    event_date=D1,
                    amount=Decimal("20.00"),
                ),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        AsyncMock(
            return_value={}
        ),
    )

    overlay = AsyncMock(
        return_value=(
            SupplierVatLiabilityComponent(
                source_id=20,
                event_date=D1,
                amount=Decimal("18.00"),
            ),
        )
    )

    monkeypatch.setattr(
        service,
        "_apply_purchase_value_correction_vat_to_supplier_components",
        overlay,
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        AsyncMock(
            return_value=()
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
    assert result[0].source_id == 20
    assert result[0].event_date == D1
    assert result[0].amount == Decimal(
        "118.00"
    )

    overlay.assert_awaited_once()


@pytest.mark.asyncio
async def test_legal_input_vat_credit_layer_is_not_supplier_input(
    monkeypatch,
):
    """
    Supplier liability calculation has no dependency on
    PurchaseValueCorrectionInputVatCreditCorrectionEvent.

    Economic VAT component stays 18 regardless of any legal
    641/644 recognition performed elsewhere.
    """

    invoice = SimpleNamespace(
        company_id=1,
    )

    allocation = SimpleNamespace(
        id=20,
        status=(
            InvoiceFulfillmentAllocationStatus.ACTIVE
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
                event_date=D1,
                amount=Decimal("100.00"),
                currency_code="UAH",
            ),
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_base_by_source",
        AsyncMock(
            return_value={}
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_supplier_vat_components",
        AsyncMock(
            return_value=(
                SupplierVatLiabilityComponent(
                    source_id=20,
                    event_date=D1,
                    amount=Decimal("20.00"),
                ),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_return_vat_by_source",
        AsyncMock(
            return_value={}
        ),
    )

    monkeypatch.setattr(
        service,
        "_apply_purchase_value_correction_vat_to_supplier_components",
        AsyncMock(
            return_value=(
                SupplierVatLiabilityComponent(
                    source_id=20,
                    event_date=D1,
                    amount=Decimal("18.00"),
                ),
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_load_active_purchase_value_correction_base_adjustments",
        AsyncMock(
            return_value=()
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

    assert result[0].amount == Decimal(
        "118.00"
    )
