import asyncio
import ast
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.purchase_return_recognition_reconciliation_service as service
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.purchase_return_recognition_calculation_service import (
    PurchaseReturnEconomicCapacity,
)
from app.services.purchase_return_recognition_persistence_service import (
    PurchaseReturnRecognitionPersistenceResult,
)
from app.services.purchase_return_recognition_reconciliation_service import (
    PurchaseReturnReceiptPeerSnapshot,
    PurchaseReturnRecognitionReconciliationDataIntegrityError,
    _build_active_return_candidates,
    _build_receipt_base_amounts,
    build_purchase_return_invoice_line_snapshot,
    reconcile_purchase_return_recognition_for_fulfillment_line,
)
from app.services.tax_types import (
    TaxDirection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


D1 = date(
    2026,
    1,
    1,
)

D2 = date(
    2026,
    1,
    2,
)

D3 = date(
    2026,
    1,
    3,
)


def _purchase_document():
    return TradeDocument(
        id=100,
        company_id=1,
        direction=TradeDirection.PURCHASE,
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        currency_code="UAH",
    )


def _invoice_line(
    *,
    taxed: bool,
):
    return TradeDocumentLine(
        id=200,
        company_id=1,
        trade_document_id=100,
        product_id=300,
        quantity=Decimal("2"),
        unit_price=Decimal("10.0000"),
        tax_rate_code=(
            "VAT20"
            if taxed
            else None
        ),
    )


def _tax_calculation(
    *,
    direction=TaxDirection.INPUT,
):
    return TaxCalculation(
        id=400,
        company_id=1,
        trade_document_id=100,
        trade_document_line_id=200,
        product_id=300,
        direction=direction,
        taxable_base=Decimal("20.00"),
        tax_amount=Decimal("4.00"),
        currency_code="UAH",
    )


def _return_event(
    event_id: int,
    *,
    day: int,
    quantity: str = "1",
    reversal_of_id=None,
):
    return TradeReturnEvent(
        id=event_id,
        direction="purchase",
        return_date=date(
            2026,
            1,
            day,
        ),
        returned_quantity=Decimal(
            quantity
        ),
        reversal_of_id=reversal_of_id,
    )


def test_taxed_purchase_invoice_snapshot_uses_tax_calculation():
    snapshot = (
        build_purchase_return_invoice_line_snapshot(
            document=_purchase_document(),
            line=_invoice_line(
                taxed=True
            ),
            calculation=_tax_calculation(),
        )
    )

    assert (
        snapshot.quantity
        == Decimal("2")
    )

    assert (
        snapshot.gross_amount
        == Decimal("24.00")
    )

    assert (
        snapshot.tax_amount
        == Decimal("4.00")
    )

    assert (
        snapshot.currency_code
        == "UAH"
    )


def test_non_tax_purchase_invoice_snapshot_uses_invoice_price():
    snapshot = (
        build_purchase_return_invoice_line_snapshot(
            document=_purchase_document(),
            line=_invoice_line(
                taxed=False
            ),
            calculation=None,
        )
    )

    assert (
        snapshot.gross_amount
        == Decimal("20.00")
    )

    assert (
        snapshot.tax_amount
        == Decimal("0.00")
    )


def test_purchase_snapshot_rejects_output_tax():
    with pytest.raises(
        PurchaseReturnRecognitionReconciliationDataIntegrityError,
        match="must be INPUT",
    ):
        build_purchase_return_invoice_line_snapshot(
            document=_purchase_document(),
            line=_invoice_line(
                taxed=True
            ),
            calculation=_tax_calculation(
                direction=TaxDirection.OUTPUT
            ),
        )


def test_purchase_snapshot_rejects_sale_invoice():
    document = _purchase_document()

    document.direction = (
        TradeDirection.SALE
    )

    with pytest.raises(
        PurchaseReturnRecognitionReconciliationDataIntegrityError,
        match="PURCHASE invoice",
    ):
        build_purchase_return_invoice_line_snapshot(
            document=document,
            line=_invoice_line(
                taxed=False
            ),
            calculation=None,
        )


def test_active_return_candidates_ignore_reversed_original():
    history = (
        _return_event(
            10,
            day=1,
        ),
        _return_event(
            11,
            day=2,
            reversal_of_id=10,
        ),
        _return_event(
            12,
            day=3,
            quantity="2",
        ),
    )

    result = (
        _build_active_return_candidates(
            history=history
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[0].source_id
        == 12
    )

    assert (
        result[0].quantity
        == Decimal("2")
    )


def test_receipt_base_rounding_uses_all_active_peers():
    peers = (
        PurchaseReturnReceiptPeerSnapshot(
            source_id=1,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
        PurchaseReturnReceiptPeerSnapshot(
            source_id=2,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
        PurchaseReturnReceiptPeerSnapshot(
            source_id=3,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("83.3333"),
            allocation_quantity=Decimal("1"),
        ),
    )

    result = _build_receipt_base_amounts(
        peers=peers,
        requested_source_ids=(
            1,
            3,
        ),
        currency_code="UAH",
    )

    assert result == {
        1: Decimal("83.33"),
        3: Decimal("83.33"),
    }


def test_receipt_base_keeps_zero_rounding_slice():
    peers = (
        PurchaseReturnReceiptPeerSnapshot(
            source_id=1,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("0.0033"),
            allocation_quantity=Decimal("1"),
        ),
        PurchaseReturnReceiptPeerSnapshot(
            source_id=2,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("0.0033"),
            allocation_quantity=Decimal("1"),
        ),
        PurchaseReturnReceiptPeerSnapshot(
            source_id=3,
            receipt_document_id=500,
            receipt_line_id=501,
            event_date=D1,
            receipt_quantity=Decimal("3"),
            receipt_price=Decimal("0.0033"),
            allocation_quantity=Decimal("1"),
        ),
    )

    result = _build_receipt_base_amounts(
        peers=peers,
        requested_source_ids=(
            1,
            2,
            3,
        ),
        currency_code="UAH",
    )

    assert sum(
        result.values(),
        Decimal("0"),
    ) == Decimal("0.01")

    assert Decimal("0.00") in set(
        result.values()
    )


def test_orchestrator_builds_desired_pair_and_persists(
    monkeypatch,
):
    active_return = _return_event(
        50,
        day=3,
    )

    capacity = PurchaseReturnEconomicCapacity(
        source_id=70,
        event_date=D1,
        quantity=Decimal("1"),
        base_amount=Decimal("10.00"),
        gross_amount=Decimal("12.00"),
        tax_amount=Decimal("2.00"),
        currency_code="UAH",
    )

    async def load_returns(
        *_args,
        **_kwargs,
    ):
        return (
            active_return,
        )

    async def load_capacities(
        *_args,
        **_kwargs,
    ):
        return (
            capacity,
        )

    async def load_history(
        *_args,
        **_kwargs,
    ):
        return ()

    persist = AsyncMock(
        return_value=(
            PurchaseReturnRecognitionPersistenceResult(
                created_events=(),
                active_event=None,
            )
        )
    )

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        load_returns,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_capacity_sources",
        load_capacities,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_return_recognition_history",
        load_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_recognition_source",
        persist,
    )

    result = asyncio.run(
        reconcile_purchase_return_recognition_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            created_by=4,
        )
    )

    assert len(
        result.desired_targets
    ) == 1

    desired = result.desired_targets[
        0
    ]

    assert desired.pair_key == (
        50,
        70,
    )

    assert (
        desired.base_amount
        == Decimal("10.00")
    )

    assert persist.await_count == 1

    kwargs = persist.await_args.kwargs

    assert kwargs[
        "trade_return_event_id"
    ] == 50

    assert kwargs[
        "invoice_fulfillment_allocation_id"
    ] == 70

    assert (
        kwargs[
            "target"
        ]
        == desired
    )


def test_active_return_without_capacity_fails_closed(
    monkeypatch,
):
    async def load_returns(
        *_args,
        **_kwargs,
    ):
        return (
            _return_event(
                50,
                day=3,
            ),
        )

    async def empty(
        *_args,
        **_kwargs,
    ):
        return ()

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        load_returns,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_capacity_sources",
        empty,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_return_recognition_history",
        empty,
    )

    with pytest.raises(
        PurchaseReturnRecognitionReconciliationDataIntegrityError,
        match="without ACTIVE purchase economic capacity",
    ):
        asyncio.run(
            reconcile_purchase_return_recognition_for_fulfillment_line(
                object(),
                company_id=1,
                fulfillment_id=2,
                fulfillment_line_id=3,
                created_by=4,
            )
        )


def test_existing_active_pair_is_reversed_when_desired_disappears(
    monkeypatch,
):
    current = PurchaseReturnRecognitionEvent(
        id=80,
        company_id=1,
        trade_return_event_id=50,
        invoice_fulfillment_allocation_id=70,
        recognition_date=D2,
        returned_quantity=Decimal("1"),
        returned_base_amount=Decimal("10.00"),
        returned_gross_amount=Decimal("12.00"),
        returned_tax_amount=Decimal("2.00"),
        currency_code="UAH",
        created_by=4,
        reversal_of_id=None,
    )

    async def empty(
        *_args,
        **_kwargs,
    ):
        return ()

    async def load_history(
        *_args,
        **_kwargs,
    ):
        return (
            current,
        )

    persist = AsyncMock(
        return_value=(
            PurchaseReturnRecognitionPersistenceResult(
                created_events=(),
                active_event=None,
            )
        )
    )

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        empty,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_capacity_sources",
        empty,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_return_recognition_history",
        load_history,
    )

    monkeypatch.setattr(
        service,
        "reconcile_purchase_return_recognition_source",
        persist,
    )

    result = asyncio.run(
        reconcile_purchase_return_recognition_for_fulfillment_line(
            object(),
            company_id=1,
            fulfillment_id=2,
            fulfillment_line_id=3,
            created_by=4,
            adjustment_date=D3,
        )
    )

    assert result.desired_targets == ()

    assert result.current_pair_keys == (
        (
            50,
            70,
        ),
    )

    assert persist.await_count == 1

    kwargs = persist.await_args.kwargs

    assert kwargs[
        "target"
    ] is None

    assert kwargs[
        "reversal_date"
    ] == D3


def test_multiple_capacity_currencies_fail_closed(
    monkeypatch,
):
    capacities = (
        PurchaseReturnEconomicCapacity(
            source_id=70,
            event_date=D1,
            quantity=Decimal("1"),
            base_amount=Decimal("10"),
            gross_amount=Decimal("12"),
            tax_amount=Decimal("2"),
            currency_code="UAH",
        ),
        PurchaseReturnEconomicCapacity(
            source_id=71,
            event_date=D1,
            quantity=Decimal("1"),
            base_amount=Decimal("10"),
            gross_amount=Decimal("12"),
            tax_amount=Decimal("2"),
            currency_code="EUR",
        ),
    )

    async def empty(
        *_args,
        **_kwargs,
    ):
        return ()

    async def load_capacities(
        *_args,
        **_kwargs,
    ):
        return capacities

    monkeypatch.setattr(
        service,
        "_load_trade_return_history",
        empty,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_capacity_sources",
        load_capacities,
    )

    monkeypatch.setattr(
        service,
        "_load_purchase_return_recognition_history",
        empty,
    )

    with pytest.raises(
        PurchaseReturnRecognitionReconciliationDataIntegrityError,
        match="multiple currencies",
    ):
        asyncio.run(
            reconcile_purchase_return_recognition_for_fulfillment_line(
                object(),
                company_id=1,
                fulfillment_id=2,
                fulfillment_line_id=3,
                created_by=4,
            )
        )


def test_reconciliation_does_not_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_reconciliation_does_not_post_journal_or_vat_lifecycle():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    forbidden = {
        "JournalEntry",
        "TaxRecognitionEvent",
        "TaxCreditEvidence",
        "InputVatFulfillmentBridgeEvent",
        "SupplierAdvanceClearingEvent",
    }

    imported_names = set()
    referenced_names = set()

    for node in ast.walk(
        tree
    ):
        if isinstance(
            node,
            ast.Import,
        ):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name.split(".")[-1]
                )

        elif isinstance(
            node,
            ast.ImportFrom,
        ):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name
                )

        elif isinstance(
            node,
            ast.Name,
        ):
            referenced_names.add(
                node.id
            )

        elif isinstance(
            node,
            ast.Attribute,
        ):
            referenced_names.add(
                node.attr
            )

    assert (
        forbidden
        & imported_names
    ) == set()

    assert (
        forbidden
        & referenced_names
    ) == set()



def test_reconciliation_never_derives_accounting_base_from_gross_minus_tax():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    forbidden = []

    for node in ast.walk(
        tree
    ):
        if not (
            isinstance(
                node,
                ast.BinOp,
            )
            and isinstance(
                node.op,
                ast.Sub,
            )
        ):
            continue

        left = (
            node.left.id
            if isinstance(
                node.left,
                ast.Name,
            )
            else (
                node.left.attr
                if isinstance(
                    node.left,
                    ast.Attribute,
                )
                else None
            )
        )

        right = (
            node.right.id
            if isinstance(
                node.right,
                ast.Name,
            )
            else (
                node.right.attr
                if isinstance(
                    node.right,
                    ast.Attribute,
                )
                else None
            )
        )

        if (
            left
            in {
                "gross_amount",
                "returned_gross_amount",
            }
            and right
            in {
                "tax_amount",
                "returned_tax_amount",
            }
        ):
            forbidden.append(
                node
            )

    assert forbidden == []
