from dataclasses import (
    FrozenInstanceError,
)
from datetime import date
from decimal import Decimal

import pytest

from app.services.trade_return_calculation_service import (
    TradeReturnCandidate,
    TradeReturnCapacityError,
    TradeReturnChronologyError,
    TradeReturnDataIntegrityError,
    TradeReturnEconomicCapacity,
    TradeReturnTarget,
    TradeValueCorrectionError,
    build_trade_return_targets,
    calculate_trade_value_correction,
)


D1 = date(
    2026,
    9,
    1,
)

D2 = date(
    2026,
    9,
    2,
)

D3 = date(
    2026,
    9,
    3,
)

D4 = date(
    2026,
    9,
    4,
)


def capacity(
    source_id,
    event_date,
    quantity,
    gross,
    tax="0.00",
    currency="UAH",
):
    return TradeReturnEconomicCapacity(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
        gross_amount=Decimal(
            gross
        ),
        tax_amount=Decimal(
            tax
        ),
        currency_code=currency,
    )


def candidate(
    source_id,
    event_date,
    quantity,
):
    return TradeReturnCandidate(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
    )


def test_empty_return_sources_produce_no_targets():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                1,
                D1,
                "2",
                "120.00",
                "20.00",
            ),
        ),
        candidates=(),
        currency_code="UAH",
    )

    assert result == ()


def test_full_return_reproduces_exact_source_amounts():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "2",
                "120.00",
                "20.00",
            ),
        ),
        candidates=(
            candidate(
                20,
                D2,
                "2",
            ),
        ),
        currency_code="UAH",
    )

    assert result == (
        TradeReturnTarget(
            return_source_id=20,
            economic_source_id=10,
            event_date=D2,
            quantity=Decimal("2"),
            gross_amount=Decimal("120.00"),
            tax_amount=Decimal("20.00"),
            currency_code="UAH",
        ),
    )

    assert (
        result[0]
        .taxable_base_amount
        == Decimal("100.00")
    )

    assert result[0].pair_key == (
        20,
        10,
    )


def test_partial_return_uses_proportional_source_amount():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "2",
                "120.00",
                "20.00",
            ),
        ),
        candidates=(
            candidate(
                20,
                D2,
                "1",
            ),
        ),
        currency_code="UAH",
    )

    assert result[0].quantity == Decimal("1")
    assert result[0].gross_amount == Decimal("60.00")
    assert result[0].tax_amount == Decimal("10.00")


def test_cumulative_rounding_preserves_full_source_total():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "3",
                "100.00",
                "20.00",
            ),
        ),
        candidates=(
            candidate(
                20,
                D2,
                "1",
            ),
            candidate(
                21,
                D3,
                "1",
            ),
            candidate(
                22,
                D4,
                "1",
            ),
        ),
        currency_code="UAH",
    )

    assert [
        item.gross_amount
        for item in result
    ] == [
        Decimal("33.33"),
        Decimal("33.34"),
        Decimal("33.33"),
    ]

    assert [
        item.tax_amount
        for item in result
    ] == [
        Decimal("6.67"),
        Decimal("6.66"),
        Decimal("6.67"),
    ]

    assert sum(
        (
            item.gross_amount
            for item in result
        ),
        Decimal("0"),
    ) == Decimal("100.00")

    assert sum(
        (
            item.tax_amount
            for item in result
        ),
        Decimal("0"),
    ) == Decimal("20.00")


def test_one_return_source_can_split_across_capacities():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "1",
                "60.00",
                "10.00",
            ),
            capacity(
                11,
                D2,
                "2",
                "120.00",
                "20.00",
            ),
        ),
        candidates=(
            candidate(
                20,
                D3,
                "2",
            ),
        ),
        currency_code="UAH",
    )

    assert [
        item.pair_key
        for item in result
    ] == [
        (
            20,
            10,
        ),
        (
            20,
            11,
        ),
    ]

    assert [
        item.quantity
        for item in result
    ] == [
        Decimal("1"),
        Decimal("1"),
    ]

    assert [
        item.gross_amount
        for item in result
    ] == [
        Decimal("60.00"),
        Decimal("60.00"),
    ]


def test_economic_capacity_is_fifo_by_date_then_id():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                30,
                D2,
                "1",
                "70.00",
            ),
            capacity(
                20,
                D1,
                "1",
                "50.00",
            ),
            capacity(
                10,
                D1,
                "1",
                "40.00",
            ),
        ),
        candidates=(
            candidate(
                100,
                D3,
                "2",
            ),
        ),
        currency_code="UAH",
    )

    assert [
        item.economic_source_id
        for item in result
    ] == [
        10,
        20,
    ]


def test_return_sources_are_fifo_by_date_then_id():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "3",
                "90.00",
            ),
        ),
        candidates=(
            candidate(
                30,
                D3,
                "1",
            ),
            candidate(
                20,
                D2,
                "1",
            ),
            candidate(
                10,
                D2,
                "1",
            ),
        ),
        currency_code="UAH",
    )

    assert [
        item.return_source_id
        for item in result
    ] == [
        10,
        20,
        30,
    ]


def test_return_cannot_consume_future_capacity():
    with pytest.raises(
        TradeReturnChronologyError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D3,
                    "1",
                    "60.00",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


def test_over_return_is_rejected_not_capped():
    with pytest.raises(
        TradeReturnCapacityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    "1",
                    "60.00",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    "2",
                ),
            ),
            currency_code="UAH",
        )


def test_return_with_no_capacity_is_rejected():
    with pytest.raises(
        TradeReturnCapacityError
    ):
        build_trade_return_targets(
            capacities=(),
            candidates=(
                candidate(
                    20,
                    D2,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


def test_duplicate_economic_source_is_rejected():
    with pytest.raises(
        TradeReturnDataIntegrityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    "1",
                    "60.00",
                ),
                capacity(
                    10,
                    D2,
                    "1",
                    "60.00",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D3,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


def test_duplicate_return_source_is_rejected():
    with pytest.raises(
        TradeReturnDataIntegrityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    "2",
                    "120.00",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    "1",
                ),
                candidate(
                    20,
                    D3,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


def test_currency_mismatch_is_rejected():
    with pytest.raises(
        TradeReturnDataIntegrityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    "1",
                    "60.00",
                    currency="EUR",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    (
        "quantity",
        "gross",
        "tax",
    ),
    (
        (
            "0",
            "60.00",
            "0.00",
        ),
        (
            "-1",
            "60.00",
            "0.00",
        ),
        (
            "1",
            "0.00",
            "0.00",
        ),
        (
            "1",
            "-1.00",
            "0.00",
        ),
        (
            "1",
            "60.00",
            "-1.00",
        ),
        (
            "1",
            "60.00",
            "61.00",
        ),
    ),
)
def test_invalid_economic_capacity_is_rejected(
    quantity,
    gross,
    tax,
):
    with pytest.raises(
        TradeReturnDataIntegrityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    quantity,
                    gross,
                    tax,
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    "1",
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "quantity",
    (
        "0",
        "-1",
    ),
)
def test_invalid_return_quantity_is_rejected(
    quantity,
):
    with pytest.raises(
        TradeReturnDataIntegrityError
    ):
        build_trade_return_targets(
            capacities=(
                capacity(
                    10,
                    D1,
                    "1",
                    "60.00",
                ),
            ),
            candidates=(
                candidate(
                    20,
                    D2,
                    quantity,
                ),
            ),
            currency_code="UAH",
        )


def test_targets_are_deterministic_under_input_permutation():
    capacities_a = (
        capacity(
            20,
            D2,
            "1",
            "60.00",
        ),
        capacity(
            10,
            D1,
            "1",
            "40.00",
        ),
    )

    capacities_b = tuple(
        reversed(
            capacities_a
        )
    )

    candidates_a = (
        candidate(
            40,
            D4,
            "1",
        ),
        candidate(
            30,
            D3,
            "1",
        ),
    )

    candidates_b = tuple(
        reversed(
            candidates_a
        )
    )

    first = build_trade_return_targets(
        capacities=capacities_a,
        candidates=candidates_a,
        currency_code="UAH",
    )

    second = build_trade_return_targets(
        capacities=capacities_b,
        candidates=candidates_b,
        currency_code="UAH",
    )

    assert first == second


def test_target_is_immutable():
    result = build_trade_return_targets(
        capacities=(
            capacity(
                10,
                D1,
                "1",
                "60.00",
            ),
        ),
        candidates=(
            candidate(
                20,
                D2,
                "1",
            ),
        ),
        currency_code="UAH",
    )

    with pytest.raises(
        FrozenInstanceError
    ):
        result[0].quantity = Decimal(
            "2"
        )


def test_value_correction_decrease_is_signed():
    result = (
        calculate_trade_value_correction(
            original_gross_amount=(
                Decimal("120.00")
            ),
            original_tax_amount=(
                Decimal("20.00")
            ),
            corrected_gross_amount=(
                Decimal("90.00")
            ),
            corrected_tax_amount=(
                Decimal("15.00")
            ),
            currency_code="UAH",
        )
    )

    assert (
        result.gross_amount_delta
        == Decimal("-30.00")
    )

    assert (
        result.tax_amount_delta
        == Decimal("-5.00")
    )

    assert (
        result.taxable_base_delta
        == Decimal("-25.00")
    )

    assert result.is_noop is False


def test_value_correction_increase_is_signed():
    result = (
        calculate_trade_value_correction(
            original_gross_amount=(
                Decimal("100.00")
            ),
            original_tax_amount=(
                Decimal("20.00")
            ),
            corrected_gross_amount=(
                Decimal("120.00")
            ),
            corrected_tax_amount=(
                Decimal("24.00")
            ),
            currency_code="UAH",
        )
    )

    assert (
        result.gross_amount_delta
        == Decimal("20.00")
    )

    assert (
        result.tax_amount_delta
        == Decimal("4.00")
    )

    assert (
        result.taxable_base_delta
        == Decimal("16.00")
    )


def test_value_correction_noop():
    result = (
        calculate_trade_value_correction(
            original_gross_amount=(
                Decimal("120.00")
            ),
            original_tax_amount=(
                Decimal("20.00")
            ),
            corrected_gross_amount=(
                Decimal("120.00")
            ),
            corrected_tax_amount=(
                Decimal("20.00")
            ),
            currency_code="UAH",
        )
    )

    assert result.is_noop is True

    assert (
        result.gross_amount_delta
        == Decimal("0.00")
    )

    assert (
        result.tax_amount_delta
        == Decimal("0.00")
    )

    assert (
        result.taxable_base_delta
        == Decimal("0.00")
    )


@pytest.mark.parametrize(
    (
        "original_gross",
        "original_tax",
        "corrected_gross",
        "corrected_tax",
    ),
    (
        (
            "-1.00",
            "0.00",
            "1.00",
            "0.00",
        ),
        (
            "10.00",
            "11.00",
            "10.00",
            "0.00",
        ),
        (
            "10.00",
            "0.00",
            "-1.00",
            "0.00",
        ),
        (
            "10.00",
            "0.00",
            "10.00",
            "11.00",
        ),
    ),
)
def test_invalid_value_correction_state_is_rejected(
    original_gross,
    original_tax,
    corrected_gross,
    corrected_tax,
):
    with pytest.raises(
        TradeValueCorrectionError
    ):
        calculate_trade_value_correction(
            original_gross_amount=Decimal(
                original_gross
            ),
            original_tax_amount=Decimal(
                original_tax
            ),
            corrected_gross_amount=Decimal(
                corrected_gross
            ),
            corrected_tax_amount=Decimal(
                corrected_tax
            ),
            currency_code="UAH",
        )


def test_currency_code_is_normalized():
    result = (
        calculate_trade_value_correction(
            original_gross_amount=(
                Decimal("10")
            ),
            original_tax_amount=(
                Decimal("0")
            ),
            corrected_gross_amount=(
                Decimal("9")
            ),
            corrected_tax_amount=(
                Decimal("0")
            ),
            currency_code="uah",
        )
    )

    assert (
        result.currency_code
        == "UAH"
    )
