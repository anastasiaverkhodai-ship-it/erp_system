from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.sales_profitability_projection_service import (
    load_sales_profitability_projection,
)


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


def _source_row(
    *,
    recognition_id=101,
    gross="120.00",
    tax="20.00",
    cost="60.00",
):
    recognition = SimpleNamespace(
        id=recognition_id,
        invoice_fulfillment_allocation_id=201,
        recognized_gross_amount=Decimal(gross),
        recognized_tax_amount=Decimal(tax),
    )

    allocation = SimpleNamespace(
        id=201,
        fulfillment_line_id=301,
    )

    fulfillment_line = SimpleNamespace(
        id=301,
        warehouse_document_line_id=401,
    )

    cost_entry = SimpleNamespace(
        id=501,
        document_line_id=401,
        cost_amount=Decimal(cost),
    )

    return (
        recognition,
        allocation,
        fulfillment_line,
        cost_entry,
    )


def _return_event(*, gross, tax):
    return SimpleNamespace(
        recognized_gross_amount=Decimal(gross),
        recognized_tax_amount=Decimal(tax),
    )


def _cost_restoration(*, amount):
    return SimpleNamespace(
        restored_cost_amount=Decimal(amount),
    )


def _session_with_results(
    *,
    source_rows,
    return_events=(),
    cost_events=(),
    landed_events=(),
):
    session = SimpleNamespace()
    session.execute = AsyncMock(
        side_effect=[
            _RowsResult(source_rows),
            _ScalarsResult(return_events),
            _ScalarsResult(cost_events),
            _ScalarsResult(landed_events),
        ]
    )
    return session


@pytest.mark.asyncio
async def test_projection_base_sale_uses_net_revenue_and_cogs():
    session = _session_with_results(
        source_rows=[_source_row()]
    )

    result = await load_sales_profitability_projection(
        session,
        company_id=1,
        sales_recognition_event_id=101,
    )

    assert result.source.sales_recognition_event_id == 101
    assert result.source.invoice_fulfillment_allocation_id == 201
    assert result.source.fulfillment_line_id == 301
    assert result.source.warehouse_document_line_id == 401
    assert result.source.inventory_cost_entry_id == 501

    assert result.profitability.net_revenue == Decimal("100.00")
    assert result.profitability.cogs == Decimal("60.00")
    assert result.profitability.gross_profit == Decimal("40.00")
    assert (
        result.profitability.gross_margin_percent
        == Decimal("40.0000")
    )

    assert session.execute.await_count == 4


@pytest.mark.asyncio
async def test_projection_applies_active_return_revenue_and_cost():
    session = _session_with_results(
        source_rows=[
            _source_row(
                gross="240.00",
                tax="40.00",
                cost="120.00",
            )
        ],
        return_events=[
            _return_event(
                gross="60.00",
                tax="10.00",
            )
        ],
        cost_events=[
            _cost_restoration(
                amount="30.00",
            )
        ],
    )

    result = await load_sales_profitability_projection(
        session,
        company_id=1,
        sales_recognition_event_id=101,
    )

    assert (
        result.source.returned_net_revenue_amount
        == Decimal("50.00")
    )
    assert (
        result.source.restored_cost_amount
        == Decimal("30.00")
    )

    assert result.profitability.net_revenue == Decimal("150.00")
    assert result.profitability.cogs == Decimal("90.00")
    assert result.profitability.gross_profit == Decimal("60.00")
    assert (
        result.profitability.gross_margin_percent
        == Decimal("40.0000")
    )


@pytest.mark.asyncio
async def test_projection_aggregates_multiple_active_returns():
    session = _session_with_results(
        source_rows=[
            _source_row(
                gross="360.00",
                tax="60.00",
                cost="180.00",
            )
        ],
        return_events=[
            _return_event(
                gross="60.00",
                tax="10.00",
            ),
            _return_event(
                gross="30.00",
                tax="5.00",
            ),
        ],
        cost_events=[
            _cost_restoration(amount="30.00"),
            _cost_restoration(amount="15.00"),
        ],
    )

    result = await load_sales_profitability_projection(
        session,
        company_id=1,
        sales_recognition_event_id=101,
    )

    assert (
        result.source.returned_net_revenue_amount
        == Decimal("75.00")
    )
    assert (
        result.source.restored_cost_amount
        == Decimal("45.00")
    )

    assert result.profitability.net_revenue == Decimal("225.00")
    assert result.profitability.cogs == Decimal("135.00")
    assert result.profitability.gross_profit == Decimal("90.00")
    assert (
        result.profitability.gross_margin_percent
        == Decimal("40.0000")
    )


@pytest.mark.asyncio
async def test_projection_full_return_has_null_margin():
    session = _session_with_results(
        source_rows=[_source_row()],
        return_events=[
            _return_event(
                gross="120.00",
                tax="20.00",
            )
        ],
        cost_events=[
            _cost_restoration(
                amount="60.00",
            )
        ],
    )

    result = await load_sales_profitability_projection(
        session,
        company_id=1,
        sales_recognition_event_id=101,
    )

    assert result.profitability.net_revenue == Decimal("0.00")
    assert result.profitability.cogs == Decimal("0.00")
    assert result.profitability.gross_profit == Decimal("0.00")
    assert result.profitability.gross_margin_percent is None


@pytest.mark.asyncio
async def test_projection_fails_closed_when_source_missing():
    session = SimpleNamespace()
    session.execute = AsyncMock(
        return_value=_RowsResult([])
    )

    with pytest.raises(
        ValueError,
        match=(
            "active sales profitability economic "
            "source was not found"
        ),
    ):
        await load_sales_profitability_projection(
            session,
            company_id=1,
            sales_recognition_event_id=101,
        )

    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_projection_fails_closed_when_source_ambiguous():
    row = _source_row()

    session = SimpleNamespace()
    session.execute = AsyncMock(
        return_value=_RowsResult(
            [row, row]
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "sales profitability economic "
            "source is ambiguous"
        ),
    ):
        await load_sales_profitability_projection(
            session,
            company_id=1,
            sales_recognition_event_id=101,
        )

    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_projection_fails_closed_on_invalid_return_tax():
    session = _session_with_results(
        source_rows=[_source_row()],
        return_events=[
            _return_event(
                gross="10.00",
                tax="11.00",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match=(
            "sales return recognized tax "
            "exceeds gross amount"
        ),
    ):
        await load_sales_profitability_projection(
            session,
            company_id=1,
            sales_recognition_event_id=101,
        )

    # Cost query is not reached after invalid return economics.
    assert session.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("company_id", "event_id", "message"),
    [
        (
            0,
            101,
            "company_id must be greater than zero",
        ),
        (
            1,
            0,
            (
                "sales_recognition_event_id "
                "must be greater than zero"
            ),
        ),
    ],
)
async def test_projection_rejects_invalid_identity_inputs(
    company_id,
    event_id,
    message,
):
    session = SimpleNamespace()
    session.execute = AsyncMock()

    with pytest.raises(
        ValueError,
        match=message,
    ):
        await load_sales_profitability_projection(
            session,
            company_id=company_id,
            sales_recognition_event_id=event_id,
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_projection_includes_net_landed_cost_after_returns():
    session = _session_with_results(
        source_rows=[_source_row(cost="60")],
        cost_events=[_cost_restoration(amount="20")],
        landed_events=[SimpleNamespace(amount=Decimal("30"), reversal_of_id=None),
                       SimpleNamespace(amount=Decimal("30"), reversal_of_id=1),
                       SimpleNamespace(amount=Decimal("20"), reversal_of_id=None)],
    )
    result = await load_sales_profitability_projection(session, company_id=1, sales_recognition_event_id=101)
    assert result.profitability.cogs == Decimal("60")  # 60 base - 20 return + 20 net landed.
    assert result.profitability.gross_profit == Decimal("40")
