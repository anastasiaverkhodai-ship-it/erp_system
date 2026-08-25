import asyncio
from datetime import date
from decimal import Decimal

import pytest

import app.services.trade_document_lifecycle_service as lifecycle
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.trade_document_lifecycle_service import (
    SalesOrderCompanyInvalidError,
    SalesOrderContractInvalidError,
    SalesOrderCounterpartyInvalidError,
    SalesOrderProductInvalidError,
    SalesOrderWarehouseInvalidError,
    revalidate_sales_order_references,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)
from app.services.trade_document_validation import (
    TradeDocumentValidationError,
)


class FakeScalarCollection:
    def __init__(
        self,
        values,
    ) -> None:
        self._values = values

    def all(self):
        return list(
            self._values
        )


class FakeResult:
    def __init__(
        self,
        *,
        scalar=None,
        values=None,
    ) -> None:
        self._scalar = scalar
        self._values = (
            []
            if values is None
            else values
        )

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return FakeScalarCollection(
            self._values
        )


class FakeDB:
    def __init__(
        self,
        results,
    ) -> None:
        self._results = list(
            results
        )

    async def execute(
        self,
        _statement,
    ):
        if not self._results:
            raise AssertionError(
                "Unexpected database query"
            )

        return self._results.pop(
            0
        )


def _document(
    *,
    contract_id=None,
) -> TradeDocument:
    document = TradeDocument(
        id=100,
        company_id=1,
        counterparty_id=10,
        contract_id=contract_id,
        number="SO-REF-TEST",
        direction=TradeDirection.SALE,
        kind=TradeDocumentKind.ORDER,
        status=TradeDocumentStatus.DRAFT,
        document_date=date(
            2026,
            8,
            25,
        ),
        currency_code="UAH",
        payment_term_days=0,
        created_by=1,
    )

    document.lines = [
        TradeDocumentLine(
            id=200,
            company_id=1,
            trade_document_id=100,
            line_number=1,
            product_id=30,
            warehouse_id=40,
            quantity=Decimal("2.0000"),
            unit_price=Decimal("100.0000"),
        )
    ]

    return document


def test_valid_references_pass() -> None:
    db = FakeDB(
        [
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                values=[30]
            ),
            FakeResult(
                values=[40]
            ),
        ]
    )

    asyncio.run(
        revalidate_sales_order_references(
            db,
            document=_document(),
        )
    )


def test_inactive_company_rejected() -> None:
    db = FakeDB(
        [
            FakeResult(
                scalar=None
            ),
        ]
    )

    with pytest.raises(
        SalesOrderCompanyInvalidError
    ):
        asyncio.run(
            revalidate_sales_order_references(
                db,
                document=_document(),
            )
        )


def test_inactive_counterparty_rejected() -> None:
    db = FakeDB(
        [
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=None
            ),
        ]
    )

    with pytest.raises(
        SalesOrderCounterpartyInvalidError
    ):
        asyncio.run(
            revalidate_sales_order_references(
                db,
                document=_document(),
            )
        )


def test_invalid_product_rejected() -> None:
    db = FakeDB(
        [
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                values=[]
            ),
        ]
    )

    with pytest.raises(
        SalesOrderProductInvalidError
    ):
        asyncio.run(
            revalidate_sales_order_references(
                db,
                document=_document(),
            )
        )


def test_invalid_warehouse_rejected() -> None:
    db = FakeDB(
        [
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                values=[30]
            ),
            FakeResult(
                values=[]
            ),
        ]
    )

    with pytest.raises(
        SalesOrderWarehouseInvalidError
    ):
        asyncio.run(
            revalidate_sales_order_references(
                db,
                document=_document(),
            )
        )


def test_invalid_contract_is_wrapped(
    monkeypatch,
) -> None:
    def reject_contract(
        **_kwargs,
    ) -> None:
        raise TradeDocumentValidationError(
            "Contract is no longer valid"
        )

    monkeypatch.setattr(
        lifecycle,
        "validate_trade_document_contract",
        reject_contract,
    )

    db = FakeDB(
        [
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=object()
            ),
            FakeResult(
                scalar=object()
            ),
        ]
    )

    with pytest.raises(
        SalesOrderContractInvalidError,
        match="Contract is no longer valid",
    ):
        asyncio.run(
            revalidate_sales_order_references(
                db,
                document=_document(
                    contract_id=50
                ),
            )
        )
