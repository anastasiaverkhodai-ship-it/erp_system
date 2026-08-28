from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.trade_fulfillment_service as service
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_fulfillment_service import (
    PurchaseOrderFulfillmentDuplicateLineError,
    PurchaseOrderFulfillmentRequestLine,
    PurchaseOrderFulfillmentRequestRequiredError,
    PurchaseOrderFulfillmentSourceLineNotFoundError,
    PurchaseOrderFulfillmentStatusError,
    PurchaseOrderFulfillmentTypeError,
    PurchaseOrderFulfillmentWarehouseRequiredError,
    PurchaseOrderOverFulfillmentError,
    build_purchase_order_fulfillment_plan,
    calculate_purchase_order_fulfillment_status,
    create_purchase_order_fulfillment_plan,
    validate_purchase_order_fulfillment_state,
)


def make_line(
    *,
    line_id: int,
    line_number: int,
    product_id: int,
    warehouse_id: int | None,
    quantity: str,
):
    return SimpleNamespace(
        id=line_id,
        line_number=line_number,
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity=Decimal(quantity),
        unit_price=Decimal("100.0000"),
    )


def make_order(
    *,
    status=TradeDocumentStatus.CONFIRMED,
    direction=TradeDirection.PURCHASE,
    kind=TradeDocumentKind.ORDER,
    lines=None,
):
    if lines is None:
        lines = [
            make_line(
                line_id=11,
                line_number=1,
                product_id=101,
                warehouse_id=201,
                quantity="5.0000",
            ),
        ]

    return SimpleNamespace(
        id=50,
        company_id=1,
        direction=direction,
        kind=kind,
        status=status,
        lines=lines,
    )


def request(
    line_id: int,
    quantity: str,
):
    return PurchaseOrderFulfillmentRequestLine(
        trade_document_line_id=line_id,
        quantity=Decimal(quantity),
    )


def test_purchase_fulfillment_accepts_confirmed():
    validate_purchase_order_fulfillment_state(
        make_order()
    )


def test_purchase_fulfillment_accepts_partial():
    validate_purchase_order_fulfillment_state(
        make_order(
            status=(
                TradeDocumentStatus
                .PARTIALLY_FULFILLED
            )
        )
    )


def test_purchase_fulfillment_rejects_sale():
    with pytest.raises(
        PurchaseOrderFulfillmentTypeError
    ):
        validate_purchase_order_fulfillment_state(
            make_order(
                direction=TradeDirection.SALE
            )
        )


def test_purchase_fulfillment_rejects_invoice():
    with pytest.raises(
        PurchaseOrderFulfillmentTypeError
    ):
        validate_purchase_order_fulfillment_state(
            make_order(
                kind=TradeDocumentKind.INVOICE
            )
        )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.FULFILLED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_purchase_fulfillment_rejects_invalid_status(
    status,
):
    with pytest.raises(
        PurchaseOrderFulfillmentStatusError
    ):
        validate_purchase_order_fulfillment_state(
            make_order(
                status=status
            )
        )


def test_purchase_fulfillment_requires_warehouse():
    with pytest.raises(
        PurchaseOrderFulfillmentWarehouseRequiredError
    ):
        validate_purchase_order_fulfillment_state(
            make_order(
                lines=[
                    make_line(
                        line_id=11,
                        line_number=1,
                        product_id=101,
                        warehouse_id=None,
                        quantity="5.0000",
                    )
                ]
            )
        )


def test_purchase_fulfillment_request_requires_positive_quantity():
    with pytest.raises(
        ValueError
    ):
        request(
            11,
            "0",
        )


def test_purchase_plan_requires_request_lines():
    with pytest.raises(
        PurchaseOrderFulfillmentRequestRequiredError
    ):
        create_purchase_order_fulfillment_plan(
            make_order(),
            [],
            fulfilled_quantities={
                11: Decimal("0"),
            },
        )


def test_purchase_plan_rejects_duplicate_source_line():
    with pytest.raises(
        PurchaseOrderFulfillmentDuplicateLineError
    ):
        create_purchase_order_fulfillment_plan(
            make_order(),
            [
                request(11, "1"),
                request(11, "1"),
            ],
            fulfilled_quantities={
                11: Decimal("0"),
            },
        )


def test_purchase_plan_rejects_foreign_source_line():
    with pytest.raises(
        PurchaseOrderFulfillmentSourceLineNotFoundError
    ):
        create_purchase_order_fulfillment_plan(
            make_order(),
            [
                request(999, "1"),
            ],
            fulfilled_quantities={
                11: Decimal("0"),
            },
        )


def test_purchase_plan_rejects_over_fulfillment():
    with pytest.raises(
        PurchaseOrderOverFulfillmentError
    ):
        create_purchase_order_fulfillment_plan(
            make_order(),
            [
                request(11, "2.0000"),
            ],
            fulfilled_quantities={
                11: Decimal("4.0000"),
            },
        )


def test_purchase_plan_partial_receipt():
    order = make_order()

    plan = create_purchase_order_fulfillment_plan(
        order,
        [
            request(
                11,
                "2.0000",
            ),
        ],
        fulfilled_quantities={
            11: Decimal("0"),
        },
    )

    assert len(plan.lines) == 1

    assert (
        plan.lines[0].fulfilled_before
        == Decimal("0")
    )

    assert (
        plan.lines[0].fulfilled_after
        == Decimal("2.0000")
    )

    assert (
        plan.resulting_status
        == (
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )


def test_purchase_plan_finishes_order():
    order = make_order()

    plan = create_purchase_order_fulfillment_plan(
        order,
        [
            request(
                11,
                "3.0000",
            ),
        ],
        fulfilled_quantities={
            11: Decimal("2.0000"),
        },
    )

    assert (
        plan.resulting_status
        == TradeDocumentStatus.FULFILLED
    )


def test_purchase_status_uses_all_lines():
    order = make_order(
        lines=[
            make_line(
                line_id=11,
                line_number=1,
                product_id=101,
                warehouse_id=201,
                quantity="5.0000",
            ),
            make_line(
                line_id=12,
                line_number=2,
                product_id=102,
                warehouse_id=201,
                quantity="3.0000",
            ),
        ]
    )

    assert (
        calculate_purchase_order_fulfillment_status(
            order,
            {
                11: Decimal("5.0000"),
                12: Decimal("0"),
            },
        )
        == (
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )

    assert (
        calculate_purchase_order_fulfillment_status(
            order,
            {
                11: Decimal("5.0000"),
                12: Decimal("3.0000"),
            },
        )
        == TradeDocumentStatus.FULFILLED
    )


def test_purchase_plan_has_deterministic_line_order():
    order = make_order(
        lines=[
            make_line(
                line_id=12,
                line_number=2,
                product_id=102,
                warehouse_id=202,
                quantity="3.0000",
            ),
            make_line(
                line_id=11,
                line_number=1,
                product_id=101,
                warehouse_id=201,
                quantity="5.0000",
            ),
        ]
    )

    plan = create_purchase_order_fulfillment_plan(
        order,
        [
            request(12, "1"),
            request(11, "1"),
        ],
        fulfilled_quantities={
            11: Decimal("0"),
            12: Decimal("0"),
        },
    )

    assert [
        item.source_line.id
        for item in plan.lines
    ] == [
        11,
        12,
    ]


@pytest.mark.asyncio
async def test_build_purchase_plan_loads_only_fulfillment_state(
    monkeypatch,
):
    order = make_order()

    get_fulfilled = AsyncMock(
        return_value={
            11: Decimal("2.0000"),
        }
    )

    get_reserved = AsyncMock()

    monkeypatch.setattr(
        service,
        "get_persisted_fulfilled_quantities",
        get_fulfilled,
    )

    monkeypatch.setattr(
        service,
        "get_outstanding_reservation_quantities",
        get_reserved,
    )

    plan = await build_purchase_order_fulfillment_plan(
        object(),
        document=order,
        request_lines=[
            request(
                11,
                "1.0000",
            )
        ],
    )

    assert (
        plan.lines[0].fulfilled_before
        == Decimal("2.0000")
    )

    assert (
        plan.lines[0].fulfilled_after
        == Decimal("3.0000")
    )

    get_fulfilled.assert_awaited_once()

    get_reserved.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resulting_status",
    [
        TradeDocumentStatus.PARTIALLY_FULFILLED,
        TradeDocumentStatus.FULFILLED,
    ],
)
async def test_execute_purchase_fulfillment_creates_receipt_without_reservation(
    monkeypatch,
    resulting_status,
):
    from datetime import date
    from types import SimpleNamespace

    from app.models.document import (
        Document,
        DocumentStatus,
        DocumentType,
    )
    from app.models.document_line import DocumentLine
    from app.models.trade_fulfillment import TradeFulfillment
    from app.models.trade_fulfillment_line import (
        TradeFulfillmentLine,
    )
    from app.services.trade_fulfillment_service import (
        PurchaseOrderFulfillmentPlan,
        PurchaseOrderFulfillmentPlanLine,
        execute_purchase_order_fulfillment,
    )

    source_line = make_line(
        line_id=11,
        line_number=1,
        product_id=101,
        warehouse_id=201,
        quantity="5.0000",
    )

    purchase_order = make_order(
        status=TradeDocumentStatus.CONFIRMED,
        lines=[
            source_line
        ],
    )

    plan = PurchaseOrderFulfillmentPlan(
        lines=(
            PurchaseOrderFulfillmentPlanLine(
                source_line=source_line,
                quantity=Decimal("2.0000"),
                fulfilled_before=Decimal("0"),
                fulfilled_after=Decimal("2.0000"),
            ),
        ),
        resulting_status=resulting_status,
    )

    class FakeDB:
        def __init__(self):
            self.added = []
            self.flush = AsyncMock()
            self.next_line_id = 600

        def add(self, obj):
            self.added.append(obj)

            if (
                isinstance(obj, Document)
                and obj.id is None
            ):
                obj.id = 500

            elif (
                isinstance(obj, DocumentLine)
                and obj.id is None
            ):
                obj.id = self.next_line_id
                self.next_line_id += 1

            elif (
                isinstance(obj, TradeFulfillment)
                and obj.id is None
            ):
                obj.id = 700

    db = FakeDB()

    monkeypatch.setattr(
        service,
        "get_locked_purchase_order",
        AsyncMock(
            return_value=purchase_order
        ),
    )

    monkeypatch.setattr(
        service,
        "build_purchase_order_fulfillment_plan",
        AsyncMock(
            return_value=plan
        ),
    )

    consume = AsyncMock()
    reserve = AsyncMock()

    monkeypatch.setattr(
        service,
        "consume_source_line_reservation",
        consume,
    )

    monkeypatch.setattr(
        service,
        "reserve_source_line",
        reserve,
    )

    async def fake_post_document(
        *,
        db,
        company_id,
        document_id,
        accounting_rule_id,
        created_by,
    ):
        warehouse_document = next(
            item
            for item in db.added
            if isinstance(
                item,
                Document,
            )
        )

        assert (
            warehouse_document.id
            == document_id
        )

        warehouse_document.status = (
            DocumentStatus.POSTED
        )

        return (
            warehouse_document,
            SimpleNamespace(
                id=800
            ),
        )

    monkeypatch.setattr(
        service,
        "post_document",
        fake_post_document,
    )

    result = await execute_purchase_order_fulfillment(
        db,
        company_id=1,
        trade_document_id=50,
        warehouse_document_number=(
            "RECEIPT-TEST"
        ),
        document_date=date(
            2026,
            8,
            28,
        ),
        accounting_rule_id=5,
        created_by=9,
        request_lines=[
            request(
                11,
                "2.0000",
            )
        ],
    )

    assert (
        result.purchase_order
        is purchase_order
    )

    assert (
        result.purchase_order.status
        == resulting_status
    )

    assert (
        result.warehouse_document.document_type
        == DocumentType.RECEIPT
    )

    assert (
        result.warehouse_document.status
        == DocumentStatus.POSTED
    )

    assert (
        result.fulfillment.warehouse_document_type
        == DocumentType.RECEIPT.value
    )

    warehouse_lines = [
        item
        for item in db.added
        if isinstance(
            item,
            DocumentLine,
        )
    ]

    assert len(
        warehouse_lines
    ) == 1

    assert (
        warehouse_lines[0].product_id
        == source_line.product_id
    )

    assert (
        warehouse_lines[0].warehouse_id
        == source_line.warehouse_id
    )

    assert (
        warehouse_lines[0].quantity
        == Decimal("2.0000")
    )

    assert (
        warehouse_lines[0].price
        == source_line.unit_price
    )

    mappings = [
        item
        for item in db.added
        if isinstance(
            item,
            TradeFulfillmentLine,
        )
    ]

    assert len(
        mappings
    ) == 1

    assert (
        mappings[0].trade_document_line_id
        == source_line.id
    )

    assert (
        mappings[0].warehouse_document_id
        == result.warehouse_document.id
    )

    consume.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_purchase_fulfillment_rejects_blank_document_number():
    from datetime import date

    from app.services.trade_fulfillment_service import (
        PurchaseOrderFulfillmentDocumentNumberError,
        execute_purchase_order_fulfillment,
    )

    with pytest.raises(
        PurchaseOrderFulfillmentDocumentNumberError
    ):
        await execute_purchase_order_fulfillment(
            object(),
            company_id=1,
            trade_document_id=50,
            warehouse_document_number="   ",
            document_date=date(
                2026,
                8,
                28,
            ),
            accounting_rule_id=5,
            created_by=9,
            request_lines=[
                request(
                    11,
                    "1",
                )
            ],
        )
