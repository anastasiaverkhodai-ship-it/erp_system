import ast
import inspect
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_return_vat_adjustment_reconciliation_service as service
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.services.purchase_return_vat_adjustment_reconciliation_service import (
    PurchaseReturnVatAdjustmentReconciliationDataIntegrityError,
    PurchaseReturnVatAllocationCandidate,
    PurchaseReturnVatRecognitionCandidate,
    _RequestedPrreState,
    build_invoice_vat_capacity_slices,
    build_purchase_return_vat_slices,
    reconcile_purchase_return_vat_adjustment_for_recognition_event,
)


D1 = date(
    2026,
    9,
    5,
)

D2 = date(
    2026,
    9,
    6,
)


def test_invoice_vat_capacity_is_independent_cumulative_penny_allocation():
    result = build_invoice_vat_capacity_slices(
        invoice_line_quantity=Decimal("2"),
        taxable_base=Decimal("0.03"),
        tax_amount=Decimal("0.01"),
        currency_code="UAH",
        candidates=(
            PurchaseReturnVatAllocationCandidate(
                source_id=10,
                event_date=D1,
                quantity=Decimal("1"),
            ),
            PurchaseReturnVatAllocationCandidate(
                source_id=20,
                event_date=D2,
                quantity=Decimal("1"),
            ),
        ),
    )

    assert (
        result[
            10
        ].taxable_base
        == Decimal("0.02")
    )

    assert (
        result[
            10
        ].tax_amount
        == Decimal("0.01")
    )

    assert (
        result[
            20
        ].taxable_base
        == Decimal("0.01")
    )

    assert (
        result[
            20
        ].tax_amount
        == Decimal("0.00")
    )

    assert sum(
        item.taxable_base
        for item in result.values()
    ) == Decimal("0.03")

    assert sum(
        item.tax_amount
        for item in result.values()
    ) == Decimal("0.01")


def test_invoice_vat_capacity_order_is_date_then_ifa_id():
    result = build_invoice_vat_capacity_slices(
        invoice_line_quantity=Decimal("2"),
        taxable_base=Decimal("0.03"),
        tax_amount=Decimal("0.01"),
        currency_code="UAH",
        candidates=(
            PurchaseReturnVatAllocationCandidate(
                source_id=20,
                event_date=D2,
                quantity=Decimal("1"),
            ),
            PurchaseReturnVatAllocationCandidate(
                source_id=10,
                event_date=D1,
                quantity=Decimal("1"),
            ),
        ),
    )

    assert (
        result[
            10
        ].taxable_base
        == Decimal("0.02")
    )


def test_purchase_return_vat_slice_has_same_penny_behavior():
    result = build_purchase_return_vat_slices(
        allocation_quantity=Decimal("2"),
        allocation_taxable_base=Decimal("0.03"),
        allocation_tax_amount=Decimal("0.01"),
        currency_code="UAH",
        candidates=(
            PurchaseReturnVatRecognitionCandidate(
                source_id=101,
                event_date=D1,
                quantity=Decimal("1"),
                returned_tax_amount=Decimal("0.01"),
            ),
            PurchaseReturnVatRecognitionCandidate(
                source_id=102,
                event_date=D2,
                quantity=Decimal("1"),
                returned_tax_amount=Decimal("0.00"),
            ),
        ),
    )

    assert (
        result[
            101
        ].taxable_base
        == Decimal("0.02")
    )

    assert (
        result[
            101
        ].tax_amount
        == Decimal("0.01")
    )

    assert (
        result[
            102
        ].taxable_base
        == Decimal("0.01")
    )

    assert (
        result[
            102
        ].tax_amount
        == Decimal("0.00")
    )


def test_prre_tax_crosscheck_fails_closed():
    with pytest.raises(
        PurchaseReturnVatAdjustmentReconciliationDataIntegrityError
    ):
        build_purchase_return_vat_slices(
            allocation_quantity=Decimal("2"),
            allocation_taxable_base=Decimal("0.03"),
            allocation_tax_amount=Decimal("0.01"),
            currency_code="UAH",
            candidates=(
                PurchaseReturnVatRecognitionCandidate(
                    source_id=101,
                    event_date=D1,
                    quantity=Decimal("1"),
                    returned_tax_amount=Decimal("0.00"),
                ),
            ),
        )


def test_vat_base_is_not_prre_accounting_base_or_gross_minus_tax():
    result = build_purchase_return_vat_slices(
        allocation_quantity=Decimal("2"),
        allocation_taxable_base=Decimal("0.03"),
        allocation_tax_amount=Decimal("0.01"),
        currency_code="UAH",
        candidates=(
            PurchaseReturnVatRecognitionCandidate(
                source_id=101,
                event_date=D1,
                quantity=Decimal("1"),
                returned_tax_amount=Decimal("0.01"),
            ),
        ),
    )

    assert (
        result[
            101
        ].taxable_base
        == Decimal("0.02")
    )

    # Commercial PRRE penny snapshot may be:
    # gross 0.02, tax 0.01 -> gross-tax 0.01.
    # Correct VAT taxable-base slice remains 0.02.
    assert (
        result[
            101
        ].taxable_base
        != Decimal("0.01")
    )


def _prre(
    event_id,
    *,
    reversal_of_id=None,
):
    return PurchaseReturnRecognitionEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=50,
        invoice_fulfillment_allocation_id=60,
        recognition_date=D1,
        returned_quantity=Decimal("1"),
        returned_base_amount=Decimal("999.99"),
        returned_gross_amount=Decimal("0.02"),
        returned_tax_amount=Decimal("0.01"),
        currency_code="UAH",
        created_by=7,
        reversal_of_id=reversal_of_id,
    )


def _vat_event(
    event_id,
    *,
    basis_kind="goods_received_by_supplier",
    tax_calculation_id=70,
):
    return PurchaseReturnVatAdjustmentEvent(
        id=event_id,
        company_id=1,
        purchase_return_recognition_event_id=100,
        tax_calculation_id=tax_calculation_id,
        adjustment_date=D1,
        basis_kind=basis_kind,
        adjusted_taxable_base=Decimal("0.02"),
        adjusted_tax_amount=Decimal("0.01"),
        currency_code="UAH",
        created_by=7,
        reversal_of_id=None,
    )


@pytest.mark.asyncio
async def test_active_source_reconciles_desired_target(
    monkeypatch,
):
    source = _prre(
        100
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return _RequestedPrreState(
            source_event=source,
            is_active=True,
            active_events=(
                source,
            ),
        )

    async def fake_target(
        db,
        *,
        company_id,
        state,
        adjustment_date,
        basis_kind,
    ):
        return (
            service
            .build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=100,
                tax_calculation_id=70,
                adjustment_date=D2,
                basis_kind=basis_kind,
                adjusted_taxable_base=Decimal("0.02"),
                adjusted_tax_amount=Decimal("0.01"),
                currency_code="UAH",
            )
        )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        return ()

    calls = []

    async def fake_persist(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        calls.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )

    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        fake_target,
    )

    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    result = (
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )
    )

    assert result.source_is_active is True
    assert result.source_prre_id == 100
    assert len(
        calls
    ) == 1

    assert (
        calls[
            0
        ].adjusted_taxable_base
        == Decimal("0.02")
    )


@pytest.mark.asyncio
async def test_basis_change_reverses_old_source_before_new(
    monkeypatch,
):
    source = _prre(
        100
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return _RequestedPrreState(
            source_event=source,
            is_active=True,
            active_events=(
                source,
            ),
        )

    async def fake_target(
        db,
        *,
        company_id,
        state,
        adjustment_date,
        basis_kind,
    ):
        return (
            service
            .build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=100,
                tax_calculation_id=70,
                adjustment_date=D2,
                basis_kind="refund_by_supplier",
                adjusted_taxable_base=Decimal("0.02"),
                adjusted_tax_amount=Decimal("0.01"),
                currency_code="UAH",
            )
        )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        return (
            _vat_event(
                1,
                basis_kind="goods_received_by_supplier",
            ),
        )

    calls = []

    async def fake_persist(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        calls.append(
            target
        )
        return ()

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )
    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        fake_target,
    )
    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )
    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    await reconcile_purchase_return_vat_adjustment_for_recognition_event(
        object(),
        company_id=1,
        purchase_return_recognition_event_id=100,
        adjustment_date=D2,
        basis_kind="refund_by_supplier",
        created_by=9,
    )

    assert len(
        calls
    ) == 2

    assert (
        calls[
            0
        ].basis_kind
        == "goods_received_by_supplier"
    )
    assert calls[
        0
    ].is_zero is True

    assert (
        calls[
            1
        ].basis_kind
        == "refund_by_supplier"
    )
    assert calls[
        1
    ].is_zero is False


@pytest.mark.asyncio
async def test_reversed_prre_targets_existing_vat_source_to_zero(
    monkeypatch,
):
    source = _prre(
        100
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return _RequestedPrreState(
            source_event=source,
            is_active=False,
            active_events=(),
        )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        return (
            _vat_event(
                1
            ),
        )

    calls = []

    async def fake_persist(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        calls.append(
            target
        )
        return ()

    async def should_not_load_target(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "inactive PRRE must not calculate active VAT target"
        )

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )
    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        should_not_load_target,
    )
    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )
    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    result = (
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=101,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )
    )

    assert result.source_is_active is False

    assert len(
        calls
    ) == 1

    assert calls[
        0
    ].is_zero is True


@pytest.mark.asyncio
async def test_inactive_prre_without_vat_history_is_noop(
    monkeypatch,
):
    source = _prre(
        100
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return _RequestedPrreState(
            source_event=source,
            is_active=False,
            active_events=(),
        )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        return ()

    calls = []

    async def fake_persist(
        *args,
        **kwargs,
    ):
        calls.append(
            1
        )
        return ()

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )
    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )
    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    result = (
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )
    )

    assert result.created_events == ()
    assert calls == []


@pytest.mark.asyncio
async def test_active_history_wrong_tax_calculation_fails_closed(
    monkeypatch,
):
    source = _prre(
        100
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return _RequestedPrreState(
            source_event=source,
            is_active=True,
            active_events=(
                source,
            ),
        )

    async def fake_target(
        db,
        *,
        company_id,
        state,
        adjustment_date,
        basis_kind,
    ):
        return (
            service
            .build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=100,
                tax_calculation_id=70,
                adjustment_date=D2,
                basis_kind=basis_kind,
                adjusted_taxable_base=Decimal("0.02"),
                adjusted_tax_amount=Decimal("0.01"),
                currency_code="UAH",
            )
        )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        return (
            _vat_event(
                1,
                tax_calculation_id=999,
            ),
        )

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )
    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        fake_target,
    )
    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )

    with pytest.raises(
        PurchaseReturnVatAdjustmentReconciliationDataIntegrityError
    ):
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )


def test_runtime_never_uses_prre_accounting_base_or_gross():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    runtime_attributes = {
        node.attr
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.Attribute,
        )
    }

    assert (
        "returned_base_amount"
        not in runtime_attributes
    )

    assert (
        "returned_gross_amount"
        not in runtime_attributes
    )


def test_service_contains_no_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source



def _peer_vat_event(
    event_id,
    *,
    source_prre_id,
    basis_kind="goods_received_by_supplier",
    taxable_base="0.02",
    tax_amount="0.01",
):
    return PurchaseReturnVatAdjustmentEvent(
        id=event_id,
        company_id=1,
        purchase_return_recognition_event_id=(
            source_prre_id
        ),
        tax_calculation_id=70,
        adjustment_date=D1,
        basis_kind=basis_kind,
        adjusted_taxable_base=Decimal(
            taxable_base
        ),
        adjusted_tax_amount=Decimal(
            tax_amount
        ),
        currency_code="UAH",
        created_by=7,
        reversal_of_id=None,
    )


@pytest.mark.asyncio
async def test_reversed_earlier_prre_reconciles_existing_active_vat_peer(
    monkeypatch,
):
    source = _prre(
        100
    )

    peer = _prre(
        200
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        assert company_id == 1
        assert requested_event_id == 101

        return service._RequestedPrreState(
            source_event=source,
            is_active=False,
            active_events=(
                peer,
            ),
        )

    old_source_vat = _peer_vat_event(
        1,
        source_prre_id=100,
        basis_kind="refund_by_supplier",
        taxable_base="0.01",
        tax_amount="0.01",
    )

    peer_vat = _peer_vat_event(
        2,
        source_prre_id=200,
        basis_kind="goods_received_by_supplier",
        taxable_base="0.01",
        tax_amount="0.01",
    )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        assert company_id == 1

        if source_prre_id == 100:
            return (
                old_source_vat,
            )

        if source_prre_id == 200:
            return (
                peer_vat,
            )

        raise AssertionError(
            f"Unexpected PRRE VAT history: {source_prre_id}"
        )

    loaded_peer_basis = []

    async def fake_target(
        db,
        *,
        company_id,
        state,
        adjustment_date,
        basis_kind,
    ):
        assert company_id == 1
        assert state.source_event.id == 200
        assert state.is_active is True
        assert state.active_events == (
            peer,
        )
        assert adjustment_date == D2

        loaded_peer_basis.append(
            basis_kind
        )

        return (
            service
            .build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=200,
                tax_calculation_id=70,
                adjustment_date=D2,
                basis_kind=basis_kind,
                adjusted_taxable_base=Decimal(
                    "0.02"
                ),
                adjusted_tax_amount=Decimal(
                    "0.01"
                ),
                currency_code="UAH",
            )
        )

    calls = []

    async def fake_persist(
        db,
        *,
        company_id,
        target,
        created_by,
        reversal_date,
    ):
        assert company_id == 1
        assert created_by == 9
        assert reversal_date == D2

        calls.append(
            target
        )

        return ()

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )

    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        fake_target,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    result = (
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=101,
            adjustment_date=D2,
            basis_kind="refund_by_supplier",
            created_by=9,
        )
    )

    assert result.source_is_active is False
    assert result.source_prre_id == 100

    assert loaded_peer_basis == [
        "goods_received_by_supplier"
    ]

    assert len(
        calls
    ) == 2

    assert (
        calls[
            0
        ].purchase_return_recognition_event_id
        == 100
    )

    assert calls[
        0
    ].is_zero is True

    assert (
        calls[
            0
        ].basis_kind
        == "refund_by_supplier"
    )

    assert (
        calls[
            1
        ].purchase_return_recognition_event_id
        == 200
    )

    assert calls[
        1
    ].is_zero is False

    assert (
        calls[
            1
        ].basis_kind
        == "goods_received_by_supplier"
    )

    assert (
        calls[
            1
        ].adjusted_taxable_base
        == Decimal(
            "0.02"
        )
    )

    assert (
        calls[
            1
        ].adjusted_tax_amount
        == Decimal(
            "0.01"
        )
    )


@pytest.mark.asyncio
async def test_unchanged_active_vat_peer_does_not_churn_on_later_date(
    monkeypatch,
):
    source = _prre(
        100
    )

    peer = _prre(
        200
    )

    async def fake_state(
        db,
        *,
        company_id,
        requested_event_id,
    ):
        return service._RequestedPrreState(
            source_event=source,
            is_active=False,
            active_events=(
                peer,
            ),
        )

    peer_vat = _peer_vat_event(
        2,
        source_prre_id=200,
        basis_kind="goods_received_by_supplier",
        taxable_base="0.02",
        tax_amount="0.01",
    )

    async def fake_history(
        db,
        *,
        company_id,
        source_prre_id,
    ):
        if source_prre_id == 100:
            return ()

        if source_prre_id == 200:
            return (
                peer_vat,
            )

        raise AssertionError(
            f"Unexpected PRRE VAT history: {source_prre_id}"
        )

    async def fake_target(
        db,
        *,
        company_id,
        state,
        adjustment_date,
        basis_kind,
    ):
        assert state.source_event.id == 200
        assert adjustment_date == D2

        return (
            service
            .build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=200,
                tax_calculation_id=70,
                adjustment_date=D2,
                basis_kind=basis_kind,
                adjusted_taxable_base=Decimal(
                    "0.02"
                ),
                adjusted_tax_amount=Decimal(
                    "0.01"
                ),
                currency_code="UAH",
            )
        )

    calls = []

    async def fake_persist(
        *args,
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        return ()

    monkeypatch.setattr(
        service,
        "_load_requested_prre_state",
        fake_state,
    )

    monkeypatch.setattr(
        service,
        "_load_vat_history",
        fake_history,
    )

    monkeypatch.setattr(
        service,
        "_load_active_source_target",
        fake_target,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_source",
        fake_persist,
    )

    result = (
        await reconcile_purchase_return_vat_adjustment_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=101,
            adjustment_date=D2,
            basis_kind="refund_by_supplier",
            created_by=9,
        )
    )

    assert result.source_is_active is False

    assert calls == []
