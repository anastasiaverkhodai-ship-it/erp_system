from decimal import Decimal

import pytest

from app.models.trade_document import TradeDocument
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_fulfillment_service import (
    SalesOrderFulfillmentDuplicateLineError,
    SalesOrderFulfillmentRequestLine,
    SalesOrderFulfillmentSourceLineNotFoundError,
    SalesOrderFulfillmentStatusError,
    SalesOrderFulfillmentTypeError,
    SalesOrderFulfillmentReservationStateError,
    SalesOrderOverFulfillmentError,
    calculate_sales_order_fulfillment_status,
    create_sales_order_fulfillment_plan,
    validate_sales_order_fulfillment_state,
)


def make_order(
    *,
    status: TradeDocumentStatus = (
        TradeDocumentStatus.CONFIRMED
    ),
    direction: TradeDirection = (
        TradeDirection.SALE
    ),
    kind: TradeDocumentKind = (
        TradeDocumentKind.ORDER
    ),
) -> TradeDocument:
    document = TradeDocument(
        id=100,
        company_id=1,
        direction=direction,
        kind=kind,
        status=status,
    )

    document.lines = [
        TradeDocumentLine(
            id=11,
            company_id=1,
            trade_document_id=100,
            line_number=1,
            product_id=101,
            warehouse_id=201,
            quantity=Decimal("5"),
            unit_price=Decimal("10"),
        ),
        TradeDocumentLine(
            id=12,
            company_id=1,
            trade_document_id=100,
            line_number=2,
            product_id=102,
            warehouse_id=202,
            quantity=Decimal("3"),
            unit_price=Decimal("20"),
        ),
    ]

    return document


def state_maps(
    document: TradeDocument,
    *,
    fulfilled: dict[
        int,
        Decimal,
    ] | None = None,
):
    fulfilled = fulfilled or {}

    fulfilled_map = {
        line.id: Decimal(
            fulfilled.get(
                line.id,
                Decimal("0"),
            )
        )
        for line in document.lines
    }

    reserved_map = {
        line.id: (
            Decimal(line.quantity)
            - fulfilled_map[line.id]
        )
        for line in document.lines
    }

    return (
        fulfilled_map,
        reserved_map,
    )


def test_request_line_normalizes_quantity() -> None:
    request = (
        SalesOrderFulfillmentRequestLine(
            trade_document_line_id=11,
            quantity=2,
        )
    )

    assert (
        request.quantity
        == Decimal("2")
    )


def test_request_line_rejects_nonpositive_id() -> None:
    with pytest.raises(ValueError):
        SalesOrderFulfillmentRequestLine(
            trade_document_line_id=0,
            quantity=Decimal("1"),
        )


def test_request_line_rejects_nonpositive_quantity() -> None:
    with pytest.raises(ValueError):
        SalesOrderFulfillmentRequestLine(
            trade_document_line_id=11,
            quantity=Decimal("0"),
        )


def test_confirmed_order_is_fulfillable() -> None:
    document = make_order()

    validate_sales_order_fulfillment_state(
        document
    )


def test_partially_fulfilled_order_is_fulfillable() -> None:
    document = make_order(
        status=(
            TradeDocumentStatus
            .PARTIALLY_FULFILLED
        )
    )

    validate_sales_order_fulfillment_state(
        document
    )


@pytest.mark.parametrize(
    (
        "direction",
        "kind",
    ),
    [
        (
            TradeDirection.PURCHASE,
            TradeDocumentKind.ORDER,
        ),
        (
            TradeDirection.SALE,
            TradeDocumentKind.INVOICE,
        ),
    ],
)
def test_non_sales_order_is_rejected(
    direction,
    kind,
) -> None:
    document = make_order(
        direction=direction,
        kind=kind,
    )

    with pytest.raises(
        SalesOrderFulfillmentTypeError
    ):
        validate_sales_order_fulfillment_state(
            document
        )


@pytest.mark.parametrize(
    "status",
    [
        TradeDocumentStatus.DRAFT,
        TradeDocumentStatus.FULFILLED,
        TradeDocumentStatus.CANCELLED,
    ],
)
def test_invalid_lifecycle_status_is_rejected(
    status,
) -> None:
    document = make_order(
        status=status
    )

    with pytest.raises(
        SalesOrderFulfillmentStatusError
    ):
        validate_sales_order_fulfillment_state(
            document
        )


def test_duplicate_requested_source_line_is_rejected() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document
    )

    with pytest.raises(
        SalesOrderFulfillmentDuplicateLineError
    ):
        create_sales_order_fulfillment_plan(
            document,
            [
                SalesOrderFulfillmentRequestLine(
                    11,
                    Decimal("1"),
                ),
                SalesOrderFulfillmentRequestLine(
                    11,
                    Decimal("1"),
                ),
            ],
            fulfilled_quantities=fulfilled,
            reserved_quantities=reserved,
        )


def test_foreign_source_line_is_rejected() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document
    )

    with pytest.raises(
        SalesOrderFulfillmentSourceLineNotFoundError
    ):
        create_sales_order_fulfillment_plan(
            document,
            [
                SalesOrderFulfillmentRequestLine(
                    999,
                    Decimal("1"),
                )
            ],
            fulfilled_quantities=fulfilled,
            reserved_quantities=reserved,
        )


def test_over_fulfillment_is_rejected() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document,
        fulfilled={
            11: Decimal("4"),
        },
    )

    with pytest.raises(
        SalesOrderOverFulfillmentError
    ):
        create_sales_order_fulfillment_plan(
            document,
            [
                SalesOrderFulfillmentRequestLine(
                    11,
                    Decimal("2"),
                )
            ],
            fulfilled_quantities=fulfilled,
            reserved_quantities=reserved,
        )


def test_reservation_drift_is_rejected() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document
    )

    reserved[11] = Decimal("4")

    with pytest.raises(
        SalesOrderFulfillmentReservationStateError
    ):
        create_sales_order_fulfillment_plan(
            document,
            [
                SalesOrderFulfillmentRequestLine(
                    11,
                    Decimal("1"),
                )
            ],
            fulfilled_quantities=fulfilled,
            reserved_quantities=reserved,
        )


def test_partial_plan_results_in_partial_status() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document
    )

    plan = create_sales_order_fulfillment_plan(
        document,
        [
            SalesOrderFulfillmentRequestLine(
                11,
                Decimal("2"),
            )
        ],
        fulfilled_quantities=fulfilled,
        reserved_quantities=reserved,
    )

    assert (
        plan.resulting_status
        == TradeDocumentStatus.PARTIALLY_FULFILLED
    )

    assert (
        plan.lines[0].fulfilled_after
        == Decimal("2")
    )

    assert (
        plan.lines[0].reserved_after
        == Decimal("3")
    )


def test_full_plan_results_in_fulfilled_status() -> None:
    document = make_order()

    fulfilled, reserved = state_maps(
        document
    )

    plan = create_sales_order_fulfillment_plan(
        document,
        [
            SalesOrderFulfillmentRequestLine(
                11,
                Decimal("5"),
            ),
            SalesOrderFulfillmentRequestLine(
                12,
                Decimal("3"),
            ),
        ],
        fulfilled_quantities=fulfilled,
        reserved_quantities=reserved,
    )

    assert (
        plan.resulting_status
        == TradeDocumentStatus.FULFILLED
    )


def test_existing_partial_state_calculates_partial() -> None:
    document = make_order()

    status = (
        calculate_sales_order_fulfillment_status(
            document,
            {
                11: Decimal("2"),
                12: Decimal("0"),
            },
        )
    )

    assert (
        status
        == TradeDocumentStatus.PARTIALLY_FULFILLED
    )


def test_existing_complete_state_calculates_fulfilled() -> None:
    document = make_order()

    status = (
        calculate_sales_order_fulfillment_status(
            document,
            {
                11: Decimal("5"),
                12: Decimal("3"),
            },
        )
    )

    assert (
        status
        == TradeDocumentStatus.FULFILLED
    )


def test_plan_uses_deterministic_stock_lock_order() -> None:
    document = make_order()

    document.lines[0].warehouse_id = 300
    document.lines[1].warehouse_id = 200

    fulfilled, reserved = state_maps(
        document
    )

    plan = create_sales_order_fulfillment_plan(
        document,
        [
            SalesOrderFulfillmentRequestLine(
                11,
                Decimal("1"),
            ),
            SalesOrderFulfillmentRequestLine(
                12,
                Decimal("1"),
            ),
        ],
        fulfilled_quantities=fulfilled,
        reserved_quantities=reserved,
    )

    assert [
        item.source_line.id
        for item in plan.lines
    ] == [
        11,
        12,
    ]


def test_lifecycle_and_fulfillment_use_same_stock_lock_order() -> None:
    from app.services.trade_document_lifecycle_service import (
        reservation_lock_order,
    )

    # reservation_lock_order belongs to confirmation,
    # therefore lifecycle validation requires DRAFT.
    document = make_order(
        status=TradeDocumentStatus.DRAFT
    )

    # Deliberately conflict product and warehouse ordering.
    #
    # line 11:
    #   product 101
    #   warehouse 300
    #
    # line 12:
    #   product 102
    #   warehouse 200
    #
    # Product-first global order must therefore be 11, 12.
    document.lines[0].warehouse_id = 300
    document.lines[1].warehouse_id = 200

    lifecycle_lines = reservation_lock_order(
        document
    )

    lifecycle_ids = [
        line.id
        for line in lifecycle_lines
    ]

    assert lifecycle_ids == [
        11,
        12,
    ]

    # Fulfillment begins only after confirmation.
    document.status = TradeDocumentStatus.CONFIRMED

    fulfilled, reserved = state_maps(
        document
    )

    plan = create_sales_order_fulfillment_plan(
        document,
        [
            SalesOrderFulfillmentRequestLine(
                11,
                Decimal("1"),
            ),
            SalesOrderFulfillmentRequestLine(
                12,
                Decimal("1"),
            ),
        ],
        fulfilled_quantities=fulfilled,
        reserved_quantities=reserved,
    )

    fulfillment_ids = [
        item.source_line.id
        for item in plan.lines
    ]

    assert fulfillment_ids == [
        11,
        12,
    ]

    assert (
        lifecycle_ids
        == fulfillment_ids
    )


@pytest.mark.asyncio
async def test_execute_fulfillment_orchestrates_one_transaction(
    monkeypatch,
) -> None:
    from datetime import date

    import app.services.trade_fulfillment_service as service
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
        SalesOrderFulfillmentExecutionResult,
        SalesOrderFulfillmentPlan,
        SalesOrderFulfillmentPlanLine,
    )

    events: list[str] = []

    class FakeDB:
        def __init__(self):
            self.added = []
            self.next_id = 1000

        def add(self, obj):
            self.added.append(obj)

            events.append(
                f"add:{type(obj).__name__}"
            )

        async def flush(self):
            events.append("flush")

            for obj in self.added:
                if getattr(
                    obj,
                    "id",
                    None,
                ) is None:
                    obj.id = self.next_id
                    self.next_id += 1

    db = FakeDB()

    sales_order = make_order(
        status=TradeDocumentStatus.CONFIRMED
    )

    source_line = sales_order.lines[0]

    plan = SalesOrderFulfillmentPlan(
        lines=(
            SalesOrderFulfillmentPlanLine(
                source_line=source_line,
                quantity=Decimal("2"),
                fulfilled_before=Decimal("0"),
                fulfilled_after=Decimal("2"),
                reserved_before=Decimal("5"),
                reserved_after=Decimal("3"),
            ),
        ),
        resulting_status=(
            TradeDocumentStatus.PARTIALLY_FULFILLED
        ),
    )

    async def fake_get_locked_trade_document(
        db,
        *,
        company_id,
        document_id,
    ):
        events.append("lock-sales-order")

        assert company_id == 1
        assert document_id == 100

        return sales_order

    async def fake_build_plan(
        db,
        *,
        document,
        request_lines,
    ):
        events.append("build-plan")

        assert document is sales_order
        assert len(request_lines) == 1

        return plan

    async def fake_validate_accounting_rule(
        db,
        *,
        company_id,
        accounting_rule_id,
    ):
        events.append(
            "validate-accounting-rule"
        )
        assert company_id == 1
        assert accounting_rule_id == 77
        return object()

    consume_calls = []

    async def fake_consume(
        db,
        *,
        company_id,
        source_document_id,
        source_document_line_id,
        quantity,
    ):
        events.append("consume")

        consume_calls.append(
            (
                company_id,
                source_document_id,
                source_document_line_id,
                quantity,
            )
        )

        return object()

    async def fake_post_document(
        *,
        db,
        company_id,
        document_id,
        accounting_rule_id,
        created_by,
    ):
        events.append("post-document")

        document = next(
            obj
            for obj in db.added
            if (
                isinstance(obj, Document)
                and obj.id == document_id
            )
        )

        document.status = (
            DocumentStatus.POSTED
        )

        return (
            document,
            object(),
        )

    monkeypatch.setattr(
        service,
        "get_locked_trade_document",
        fake_get_locked_trade_document,
    )

    monkeypatch.setattr(
        service,
        "build_sales_order_fulfillment_plan",
        fake_build_plan,
    )

    monkeypatch.setattr(
        service,
        "validate_sales_fulfillment_accounting_rule_for_company",
        fake_validate_accounting_rule,
    )

    monkeypatch.setattr(
        service,
        "consume_source_line_reservation",
        fake_consume,
    )

    monkeypatch.setattr(
        service,
        "post_document",
        fake_post_document,
    )

    request_line = (
        SalesOrderFulfillmentRequestLine(
            trade_document_line_id=11,
            quantity=Decimal("2"),
        )
    )

    result = (
        await service.execute_sales_order_fulfillment(
            db,
            company_id=1,
            trade_document_id=100,
            warehouse_document_number="ISSUE-1",
            document_date=date(2026, 8, 27),
            accounting_rule_id=77,
            created_by=9,
            request_lines=[
                request_line
            ],
        )
    )

    assert isinstance(
        result,
        SalesOrderFulfillmentExecutionResult,
    )

    assert (
        result.sales_order.status
        == TradeDocumentStatus.PARTIALLY_FULFILLED
    )

    assert (
        result.warehouse_document.document_type
        == DocumentType.ISSUE
    )

    assert (
        result.warehouse_document.status
        == DocumentStatus.POSTED
    )

    assert (
        result.warehouse_document.number
        == "ISSUE-1"
    )

    assert (
        result.warehouse_document.accounting_rule_id
        == 77
    )

    warehouse_lines = [
        obj
        for obj in db.added
        if isinstance(
            obj,
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
        == Decimal("2")
    )

    assert (
        warehouse_lines[0].price
        == Decimal(
            source_line.unit_price
        )
    )

    fulfillment_headers = [
        obj
        for obj in db.added
        if isinstance(
            obj,
            TradeFulfillment,
        )
    ]

    assert len(
        fulfillment_headers
    ) == 1

    fulfillment_lines = [
        obj
        for obj in db.added
        if isinstance(
            obj,
            TradeFulfillmentLine,
        )
    ]

    assert len(
        fulfillment_lines
    ) == 1

    assert (
        fulfillment_lines[0]
        .trade_document_line_id
        == source_line.id
    )

    assert (
        fulfillment_lines[0]
        .warehouse_document_line_id
        == warehouse_lines[0].id
    )

    assert (
        events.index(
            "validate-accounting-rule"
        )
        < events.index(
            "build-plan"
        )
    )

    assert consume_calls == [
        (
            1,
            100,
            11,
            Decimal("2"),
        )
    ]

    assert (
        events.index("consume")
        < events.index("post-document")
    )

    # Executor deliberately has no commit / rollback.
    assert not hasattr(
        db,
        "commit",
    )

    assert not hasattr(
        db,
        "rollback",
    )
