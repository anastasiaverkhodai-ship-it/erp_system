import ast
import inspect
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_return_recognition_calculation_service as service
from app.services.purchase_return_recognition_calculation_service import (
    PurchaseReturnEconomicCapacity,
    PurchaseReturnRecognitionDataIntegrityError,
    build_purchase_return_recognition_targets,
)
from app.services.trade_return_calculation_service import (
    TradeReturnCandidate,
    TradeReturnChronologyError,
)


def _capacity(
    source_id: int,
    *,
    day: int = 1,
    quantity: str = "2",
    base: str = "10.00",
    gross: str = "12.00",
    tax: str = "2.00",
):
    return PurchaseReturnEconomicCapacity(
        source_id=source_id,
        event_date=date(
            2026,
            1,
            day,
        ),
        quantity=Decimal(
            quantity
        ),
        base_amount=Decimal(
            base
        ),
        gross_amount=Decimal(
            gross
        ),
        tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
    )


def _return(
    source_id: int,
    *,
    day: int = 10,
    quantity: str = "1",
):
    return TradeReturnCandidate(
        source_id=source_id,
        event_date=date(
            2026,
            1,
            day,
        ),
        quantity=Decimal(
            quantity
        ),
    )


def test_historical_base_is_independent_from_gross_minus_tax():
    result = build_purchase_return_recognition_targets(
        capacities=(
            _capacity(
                10,
                quantity="2",
                base="0.03",
                gross="0.04",
                tax="0.01",
            ),
        ),
        candidates=(
            _return(
                100,
                quantity="1",
            ),
        ),
        currency_code="UAH",
    )

    assert len(
        result
    ) == 1

    target = result[0]

    assert (
        target.base_amount
        == Decimal("0.02")
    )

    assert (
        target.gross_amount
        == Decimal("0.02")
    )

    assert (
        target.tax_amount
        == Decimal("0.01")
    )

    assert (
        target.gross_amount
        - target.tax_amount
        == Decimal("0.01")
    )

    assert (
        target.base_amount
        != (
            target.gross_amount
            - target.tax_amount
        )
    )


def test_full_return_closes_exact_historical_base():
    result = build_purchase_return_recognition_targets(
        capacities=(
            _capacity(
                10,
                quantity="2",
                base="0.03",
                gross="0.04",
                tax="0.01",
            ),
        ),
        candidates=(
            _return(
                100,
                day=10,
                quantity="1",
            ),
            _return(
                101,
                day=11,
                quantity="1",
            ),
        ),
        currency_code="UAH",
    )

    assert tuple(
        target.base_amount
        for target in result
    ) == (
        Decimal("0.02"),
        Decimal("0.01"),
    )

    assert sum(
        (
            target.base_amount
            for target in result
        ),
        Decimal("0"),
    ) == Decimal("0.03")


def test_positive_quantity_may_have_zero_base_slice():
    result = build_purchase_return_recognition_targets(
        capacities=(
            _capacity(
                10,
                quantity="3",
                base="0.01",
                gross="0.03",
                tax="0.00",
            ),
        ),
        candidates=(
            _return(
                100,
                quantity="1",
            ),
        ),
        currency_code="UAH",
    )

    assert (
        result[0].quantity
        == Decimal("1")
    )

    assert (
        result[0].base_amount
        == Decimal("0.00")
    )


def test_one_return_can_split_across_purchase_capacities():
    result = build_purchase_return_recognition_targets(
        capacities=(
            _capacity(
                10,
                day=1,
                quantity="1",
                base="10.00",
                gross="12.00",
                tax="2.00",
            ),
            _capacity(
                11,
                day=2,
                quantity="2",
                base="20.00",
                gross="24.00",
                tax="4.00",
            ),
        ),
        candidates=(
            _return(
                100,
                quantity="2",
            ),
        ),
        currency_code="UAH",
    )

    assert tuple(
        (
            target.economic_source_id,
            target.quantity,
            target.base_amount,
        )
        for target in result
    ) == (
        (
            10,
            Decimal("1"),
            Decimal("10.00"),
        ),
        (
            11,
            Decimal("1"),
            Decimal("10.00"),
        ),
    )


def test_negative_historical_base_is_rejected():
    with pytest.raises(
        PurchaseReturnRecognitionDataIntegrityError,
        match="base_amount cannot be negative",
    ):
        build_purchase_return_recognition_targets(
            capacities=(
                _capacity(
                    10,
                    base="-0.01",
                ),
            ),
            candidates=(
                _return(
                    100,
                ),
            ),
            currency_code="UAH",
        )


def test_trade_return_chronology_guard_is_preserved():
    with pytest.raises(
        TradeReturnChronologyError,
    ):
        build_purchase_return_recognition_targets(
            capacities=(
                _capacity(
                    10,
                    day=20,
                ),
            ),
            candidates=(
                _return(
                    100,
                    day=10,
                ),
            ),
            currency_code="UAH",
        )


def test_calculator_never_derives_base_from_gross_minus_tax():
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

    assert (
        "taxable_base_amount"
        not in source
    )
