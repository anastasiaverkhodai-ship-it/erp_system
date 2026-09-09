from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_value_correction_moving_average_peer_source_loader import (
    PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError,
    _build_peer_source_from_rows,
)


def _base():
    return SimpleNamespace(
        company_id=1,
        product_id=10,
        warehouse_id=20,
        currency_code="UAH",
        purchase_value_correction_allocation_event_id=100,
    )


def _receipt():
    return SimpleNamespace(
        company_id=1,
        product_id=10,
        warehouse_id=20,
        document_id=500,
        document_line_id=501,
    )


def _event(
    event_id,
    ifa_id,
    original,
    corrected,
    *,
    reversal_of_id=None,
    currency="UAH",
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        invoice_fulfillment_allocation_id=ifa_id,
        original_allocated_base_amount=Decimal(original),
        corrected_allocated_base_amount=Decimal(corrected),
        recognition_date=date(2026, 1, 5),
        currency_code=currency,
        reversal_of_id=reversal_of_id,
    )


def _ifa(
    ifa_id,
    line_id,
    *,
    status="active",
):
    return SimpleNamespace(
        id=ifa_id,
        company_id=1,
        fulfillment_line_id=line_id,
        status=status,
    )


def _line(
    line_id,
    *,
    document_id=500,
    document_line_id=501,
    product_id=10,
    warehouse_id=20,
):
    return SimpleNamespace(
        id=line_id,
        company_id=1,
        warehouse_document_id=document_id,
        warehouse_document_line_id=document_line_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
    )


def test_loads_all_active_peers_for_same_physical_receipt():
    result = _build_peer_source_from_rows(
        base_source=_base(),
        source_receipt_movement=_receipt(),
        allocation_events=(
            _event(
                100,
                1000,
                "60",
                "54",
            ),
            _event(
                101,
                1001,
                "40",
                "36",
            ),
        ),
        allocations_by_id={
            1000: _ifa(
                1000,
                2000,
            ),
            1001: _ifa(
                1001,
                2001,
            ),
        },
        fulfillment_lines_by_id={
            2000: _line(2000),
            2001: _line(2001),
        },
    )

    assert set(
        result.allocation_event_ids
    ) == {
        100,
        101,
    }

    deltas = {
        peer.allocation_event_id:
        peer.allocation_value_delta
        for peer in result.peers
    }

    assert deltas[100] == Decimal("-6")
    assert deltas[101] == Decimal("-4")


def test_other_physical_receipt_is_not_peer():
    result = _build_peer_source_from_rows(
        base_source=_base(),
        source_receipt_movement=_receipt(),
        allocation_events=(
            _event(
                100,
                1000,
                "60",
                "54",
            ),
            _event(
                101,
                1001,
                "40",
                "36",
            ),
        ),
        allocations_by_id={
            1000: _ifa(
                1000,
                2000,
            ),
            1001: _ifa(
                1001,
                2001,
            ),
        },
        fulfillment_lines_by_id={
            2000: _line(2000),
            2001: _line(
                2001,
                document_line_id=999,
            ),
        },
    )

    assert (
        result.allocation_event_ids
        == (100,)
    )


def test_reversed_original_is_not_active_peer():
    result = _build_peer_source_from_rows(
        base_source=_base(),
        source_receipt_movement=_receipt(),
        allocation_events=(
            _event(
                100,
                1000,
                "60",
                "54",
            ),
            _event(
                101,
                1001,
                "40",
                "36",
            ),
            _event(
                102,
                1001,
                "36",
                "40",
                reversal_of_id=101,
            ),
        ),
        allocations_by_id={
            1000: _ifa(
                1000,
                2000,
            ),
            1001: _ifa(
                1001,
                2001,
            ),
        },
        fulfillment_lines_by_id={
            2000: _line(2000),
            2001: _line(2001),
        },
    )

    assert (
        result.allocation_event_ids
        == (100,)
    )


def test_inactive_ifa_is_not_peer():
    result = _build_peer_source_from_rows(
        base_source=_base(),
        source_receipt_movement=_receipt(),
        allocation_events=(
            _event(
                100,
                1000,
                "60",
                "54",
            ),
            _event(
                101,
                1001,
                "40",
                "36",
            ),
        ),
        allocations_by_id={
            1000: _ifa(
                1000,
                2000,
            ),
            1001: _ifa(
                1001,
                2001,
                status="reversed",
            ),
        },
        fulfillment_lines_by_id={
            2000: _line(2000),
            2001: _line(2001),
        },
    )

    assert (
        result.allocation_event_ids
        == (100,)
    )


def test_peer_currency_mismatch_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError
    ):
        _build_peer_source_from_rows(
            base_source=_base(),
            source_receipt_movement=_receipt(),
            allocation_events=(
                _event(
                    100,
                    1000,
                    "60",
                    "54",
                ),
                _event(
                    101,
                    1001,
                    "40",
                    "36",
                    currency="EUR",
                ),
            ),
            allocations_by_id={
                1000: _ifa(
                    1000,
                    2000,
                ),
                1001: _ifa(
                    1001,
                    2001,
                ),
            },
            fulfillment_lines_by_id={
                2000: _line(2000),
                2001: _line(2001),
            },
        )


def test_duplicate_reversal_graph_fails_closed():
    with pytest.raises(
        PurchaseValueCorrectionMovingAveragePeerSourceIntegrityError
    ):
        _build_peer_source_from_rows(
            base_source=_base(),
            source_receipt_movement=_receipt(),
            allocation_events=(
                _event(
                    100,
                    1000,
                    "60",
                    "54",
                ),
                _event(
                    101,
                    1000,
                    "54",
                    "60",
                    reversal_of_id=100,
                ),
                _event(
                    102,
                    1000,
                    "54",
                    "60",
                    reversal_of_id=100,
                ),
            ),
            allocations_by_id={
                1000: _ifa(
                    1000,
                    2000,
                ),
            },
            fulfillment_lines_by_id={
                2000: _line(2000),
            },
        )
