import ast
import inspect
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_return_vat_adjustment_lifecycle_service as service
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.services.purchase_return_vat_adjustment_journal_service import (
    PurchaseReturnVatAdjustmentJournalError,
)
from app.services.purchase_return_vat_adjustment_lifecycle_service import (
    PurchaseReturnVatAdjustmentLifecycleError,
    _post_created_purchase_return_vat_adjustment_events,
    reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event,
)
from app.services.purchase_return_vat_adjustment_reconciliation_service import (
    PurchaseReturnVatAdjustmentReconciliationError,
    PurchaseReturnVatAdjustmentReconciliationResult,
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


def event(
    event_id,
    *,
    reversal_of_id=None,
    tax="20.00",
    basis_kind="goods_received_by_supplier",
):
    return PurchaseReturnVatAdjustmentEvent(
        id=event_id,
        company_id=1,
        purchase_return_recognition_event_id=100,
        tax_calculation_id=200,
        adjustment_date=D2,
        basis_kind=basis_kind,
        adjusted_taxable_base=Decimal("100.00"),
        adjusted_tax_amount=Decimal(tax),
        currency_code="UAH",
        created_by=7,
        reversal_of_id=reversal_of_id,
    )


def result_with(
    *events,
):
    return (
        PurchaseReturnVatAdjustmentReconciliationResult(
            requested_event_id=100,
            source_prre_id=100,
            source_is_active=True,
            desired_target=None,
            current_source_keys=(),
            created_events=tuple(
                events
            ),
        )
    )


@pytest.mark.asyncio
async def test_created_events_are_posted_in_exact_order(
    monkeypatch,
):
    reversal = event(
        301,
        reversal_of_id=300,
    )

    replacement = event(
        302,
        basis_kind="refund_by_supplier",
    )

    calls = []

    async def fake_generate(
        db,
        *,
        event,
        created_by,
    ):
        calls.append(
            (
                "original",
                event.id,
                created_by,
            )
        )

        return None

    async def fake_reverse(
        db,
        *,
        reversal_event,
        reversed_by,
    ):
        calls.append(
            (
                "reversal",
                reversal_event.id,
                reversed_by,
            )
        )

        return None

    monkeypatch.setattr(
        service,
        "generate_and_post_purchase_return_vat_adjustment_journal_entry",
        fake_generate,
    )

    monkeypatch.setattr(
        service,
        "reverse_purchase_return_vat_adjustment_journal_entry",
        fake_reverse,
    )

    await (
        _post_created_purchase_return_vat_adjustment_events(
            object(),
            result=result_with(
                reversal,
                replacement,
            ),
            created_by=9,
        )
    )

    assert calls == [
        (
            "reversal",
            301,
            9,
        ),
        (
            "original",
            302,
            9,
        ),
    ]


@pytest.mark.asyncio
async def test_zero_tax_event_is_still_dispatched_to_journal_layer(
    monkeypatch,
):
    zero_event = event(
        300,
        tax="0.00",
    )

    calls = []

    async def fake_generate(
        db,
        *,
        event,
        created_by,
    ):
        calls.append(
            event.id
        )

        return None

    monkeypatch.setattr(
        service,
        "generate_and_post_purchase_return_vat_adjustment_journal_entry",
        fake_generate,
    )

    await (
        _post_created_purchase_return_vat_adjustment_events(
            object(),
            result=result_with(
                zero_event
            ),
            created_by=9,
        )
    )

    assert calls == [
        300
    ]


@pytest.mark.asyncio
async def test_lifecycle_calls_reconciliation_then_journal(
    monkeypatch,
):
    async def supplier_noop(
        *args,
        **kwargs,
    ):
        return None

    monkeypatch.setattr(
        service,
        (
            "_reconcile_supplier_advances_after_"
            "purchase_return_vat_adjustment"
        ),
        supplier_noop,
    )

    reconciliation_result = (
        result_with(
            event(
                300
            )
        )
    )

    calls = []

    async def fake_reconcile(
        db,
        *,
        company_id,
        purchase_return_recognition_event_id,
        adjustment_date,
        basis_kind,
        created_by,
    ):
        calls.append(
            (
                "reconcile",
                company_id,
                purchase_return_recognition_event_id,
                adjustment_date,
                basis_kind,
                created_by,
            )
        )

        return reconciliation_result

    async def fake_post(
        db,
        *,
        result,
        created_by,
    ):
        calls.append(
            (
                "post",
                result,
                created_by,
            )
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_for_recognition_event",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_purchase_return_vat_adjustment_events",
        fake_post,
    )

    returned = (
        await reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )
    )

    assert returned is reconciliation_result

    assert calls[
        0
    ] == (
        "reconcile",
        1,
        100,
        D2,
        "goods_received_by_supplier",
        9,
    )

    assert calls[
        1
    ] == (
        "post",
        reconciliation_result,
        9,
    )


@pytest.mark.asyncio
async def test_reconciliation_error_is_wrapped(
    monkeypatch,
):
    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        raise (
            PurchaseReturnVatAdjustmentReconciliationError(
                "bad source"
            )
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_for_recognition_event",
        fake_reconcile,
    )

    with pytest.raises(
        PurchaseReturnVatAdjustmentLifecycleError,
        match="reconciliation failed",
    ):
        await reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )


@pytest.mark.asyncio
async def test_journal_error_is_wrapped(
    monkeypatch,
):
    reconciliation_result = (
        result_with(
            event(
                300
            )
        )
    )

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        return (
            reconciliation_result
        )

    async def fake_post(
        *args,
        **kwargs,
    ):
        raise (
            PurchaseReturnVatAdjustmentJournalError(
                "posting failed"
            )
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_for_recognition_event",
        fake_reconcile,
    )

    monkeypatch.setattr(
        service,
        "_post_created_purchase_return_vat_adjustment_events",
        fake_post,
    )

    with pytest.raises(
        PurchaseReturnVatAdjustmentLifecycleError,
        match="journal posting failed",
    ):
        await reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=100,
            adjustment_date=D2,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )


@pytest.mark.parametrize(
    (
        "kwargs",
        "error_type",
    ),
    (
        (
            {
                "company_id": 0,
            },
            ValueError,
        ),
        (
            {
                "purchase_return_recognition_event_id": 0,
            },
            ValueError,
        ),
        (
            {
                "adjustment_date": "2026-09-06",
            },
            TypeError,
        ),
        (
            {
                "basis_kind": "warehouse_return",
            },
            ValueError,
        ),
        (
            {
                "created_by": 0,
            },
            ValueError,
        ),
    ),
)
@pytest.mark.asyncio
async def test_invalid_context_fails_before_reconciliation(
    monkeypatch,
    kwargs,
    error_type,
):
    called = False

    async def fake_reconcile(
        *args,
        **kwargs,
    ):
        nonlocal called

        called = True

        raise AssertionError(
            "reconciliation must not be called"
        )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_vat_adjustment_for_recognition_event",
        fake_reconcile,
    )

    values = {
        "company_id": 1,
        "purchase_return_recognition_event_id": 100,
        "adjustment_date": D2,
        "basis_kind": "goods_received_by_supplier",
        "created_by": 9,
    }

    values.update(
        kwargs
    )

    with pytest.raises(
        error_type
    ):
        await reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
            object(),
            **values,
        )

    assert called is False


def test_lifecycle_contains_no_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_lifecycle_has_no_legal_credit_coupling_and_has_supplier_wiring():
    import ast
    import inspect

    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    imported_modules = {
        node.module
        for node in ast.walk(
            tree
        )
        if (
            isinstance(
                node,
                ast.ImportFrom,
            )
            and node.module is not None
        )
    }

    for forbidden in (
        "purchase_return_input_vat_credit_correction",
        "tax_credit_evidence",
        "tax_recognition",
    ):
        assert not any(
            forbidden in module
            for module in imported_modules
        )

    assert (
        "app.services."
        "supplier_advance_clearing_lifecycle_service"
        in imported_modules
    )

    assert (
        "_reconcile_supplier_advances_after_"
        "purchase_return_vat_adjustment"
        in source
    )





@pytest.mark.asyncio
async def test_affected_invoice_ids_after_prvat_are_unique_and_sorted():
    from types import SimpleNamespace

    class RowsResult:
        def all(
            self,
        ):
            return (
                (
                    12,
                    30,
                ),
                (
                    11,
                    20,
                ),
            )

    class DB:
        async def execute(
            self,
            statement,
        ):
            return RowsResult()

    result = SimpleNamespace(
        created_events=(
            SimpleNamespace(
                purchase_return_recognition_event_id=12
            ),
            SimpleNamespace(
                purchase_return_recognition_event_id=11
            ),
            SimpleNamespace(
                purchase_return_recognition_event_id=12
            ),
        )
    )

    invoice_ids = (
        await service._load_affected_purchase_invoice_ids_after_vat_adjustment(
            DB(),
            company_id=1,
            result=result,
        )
    )

    assert invoice_ids == (
        20,
        30,
    )


@pytest.mark.asyncio
async def test_no_created_prvat_events_skip_invoice_resolution():
    from types import SimpleNamespace

    class DB:
        async def execute(
            self,
            statement,
        ):
            raise AssertionError(
                "DB query must not run without created PRVAT events"
            )

    result = SimpleNamespace(
        created_events=()
    )

    invoice_ids = (
        await service._load_affected_purchase_invoice_ids_after_vat_adjustment(
            DB(),
            company_id=1,
            result=result,
        )
    )

    assert invoice_ids == ()


@pytest.mark.asyncio
async def test_missing_prre_provenance_after_prvat_fails_closed():
    from types import SimpleNamespace

    class RowsResult:
        def all(
            self,
        ):
            return (
                (
                    11,
                    20,
                ),
            )

    class DB:
        async def execute(
            self,
            statement,
        ):
            return RowsResult()

    result = SimpleNamespace(
        created_events=(
            SimpleNamespace(
                purchase_return_recognition_event_id=11
            ),
            SimpleNamespace(
                purchase_return_recognition_event_id=12
            ),
        )
    )

    with pytest.raises(
        service.PurchaseReturnVatAdjustmentLifecycleError,
        match="missing PurchaseReturnRecognitionEvent",
    ):
        await service._load_affected_purchase_invoice_ids_after_vat_adjustment(
            DB(),
            company_id=1,
            result=result,
        )


@pytest.mark.asyncio
async def test_supplier_reconciliation_runs_once_per_affected_invoice(
    monkeypatch,
):
    from datetime import date
    from types import SimpleNamespace

    d1 = date(
        2026,
        9,
        5,
    )

    async def invoice_ids(
        *args,
        **kwargs,
    ):
        return (
            20,
            30,
        )

    calls = []

    async def supplier(
        db,
        *,
        company_id,
        invoice_id,
        adjustment_date,
        created_by,
    ):
        calls.append(
            (
                company_id,
                invoice_id,
                adjustment_date,
                created_by,
            )
        )

        return None

    monkeypatch.setattr(
        service,
        "_load_affected_purchase_invoice_ids_after_vat_adjustment",
        invoice_ids,
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        supplier,
    )

    await (
        service._reconcile_supplier_advances_after_purchase_return_vat_adjustment(
            object(),
            company_id=1,
            result=SimpleNamespace(
                created_events=()
            ),
            adjustment_date=d1,
            created_by=9,
        )
    )

    assert calls == [
        (
            1,
            20,
            d1,
            9,
        ),
        (
            1,
            30,
            d1,
            9,
        ),
    ]


@pytest.mark.asyncio
async def test_supplier_lifecycle_error_after_prvat_is_wrapped(
    monkeypatch,
):
    from datetime import date
    from types import SimpleNamespace

    async def invoice_ids(
        *args,
        **kwargs,
    ):
        return (
            20,
        )

    async def fail_supplier(
        *args,
        **kwargs,
    ):
        raise (
            service.SupplierAdvanceClearingLifecycleError(
                "supplier clearing failed"
            )
        )

    monkeypatch.setattr(
        service,
        "_load_affected_purchase_invoice_ids_after_vat_adjustment",
        invoice_ids,
    )

    monkeypatch.setattr(
        service,
        "reconcile_supplier_advance_clearing_lifecycle_for_invoice",
        fail_supplier,
    )

    with pytest.raises(
        service.PurchaseReturnVatAdjustmentLifecycleError,
        match=(
            "Supplier advance clearing after "
            "Purchase Return VAT adjustment failed"
        ),
    ):
        await (
            service._reconcile_supplier_advances_after_purchase_return_vat_adjustment(
                object(),
                company_id=1,
                result=SimpleNamespace(
                    created_events=()
                ),
                adjustment_date=date(
                    2026,
                    9,
                    5,
                ),
                created_by=9,
            )
        )


@pytest.mark.asyncio
async def test_prvat_lifecycle_runs_supplier_after_journal(
    monkeypatch,
):
    from datetime import date
    from types import SimpleNamespace

    d1 = date(
        2026,
        9,
        5,
    )

    expected = SimpleNamespace(
        created_events=()
    )

    order = []

    async def reconcile(
        *args,
        **kwargs,
    ):
        order.append(
            "reconciliation"
        )

        return expected

    async def journal(
        *args,
        **kwargs,
    ):
        order.append(
            "journal"
        )

    async def supplier(
        *args,
        **kwargs,
    ):
        order.append(
            "supplier"
        )

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_vat_adjustment_"
            "for_recognition_event"
        ),
        reconcile,
    )

    monkeypatch.setattr(
        service,
        (
            "_post_created_purchase_return_vat_"
            "adjustment_events"
        ),
        journal,
    )

    monkeypatch.setattr(
        service,
        (
            "_reconcile_supplier_advances_after_"
            "purchase_return_vat_adjustment"
        ),
        supplier,
    )

    actual = (
        await service.reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
            object(),
            company_id=1,
            purchase_return_recognition_event_id=10,
            adjustment_date=d1,
            basis_kind="goods_received_by_supplier",
            created_by=9,
        )
    )

    assert actual is expected

    assert order == [
        "reconciliation",
        "journal",
        "supplier",
    ]
