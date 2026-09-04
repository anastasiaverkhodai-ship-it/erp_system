from datetime import date
from decimal import Decimal

import pytest

from app.services.customer_advance_clearing_calculation_service import (
    CustomerAdvanceClearingAmountError,
    CustomerAdvanceClearingCurrencyError,
    CustomerAdvanceClearingSourceError,
    CustomerAdvanceClearingTarget,
    CustomerAdvanceSettlementCandidate,
    CustomerEconomicReceivableCandidate,
    build_customer_advance_clearing_targets,
)


def settlement(
    *,
    source_id: int,
    event_date: date,
    amount: str,
    currency_code: str = "UAH",
) -> CustomerAdvanceSettlementCandidate:
    return CustomerAdvanceSettlementCandidate(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code=currency_code,
    )


def receivable(
    *,
    source_id: int,
    event_date: date,
    amount: str,
    currency_code: str = "UAH",
) -> CustomerEconomicReceivableCandidate:
    return CustomerEconomicReceivableCandidate(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code=currency_code,
    )


def test_payment_first_without_sales_recognition_has_no_clearing():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(),
            currency_code="UAH",
        )
    )

    assert targets == ()


def test_sales_recognition_without_settlement_has_no_clearing():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert targets == ()


def test_payment_first_then_two_sales_recognitions_clear_60_and_60():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="60.00",
                ),
                receivable(
                    source_id=21,
                    event_date=date(
                        2026,
                        9,
                        3,
                    ),
                    amount="60.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert targets == (
        CustomerAdvanceClearingTarget(
            settlement_source_id=10,
            receivable_source_id=20,
            event_date=date(
                2026,
                9,
                2,
            ),
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
        CustomerAdvanceClearingTarget(
            settlement_source_id=10,
            receivable_source_id=21,
            event_date=date(
                2026,
                9,
                3,
            ),
            amount=Decimal(
                "60.00"
            ),
            currency_code="UAH",
        ),
    )


def test_receivable_first_then_partial_settlements_clear_fifo():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="40.00",
                ),
                settlement(
                    source_id=11,
                    event_date=date(
                        2026,
                        9,
                        3,
                    ),
                    amount="80.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert [
        target.amount
        for target in targets
    ] == [
        Decimal("40.00"),
        Decimal("80.00"),
    ]

    assert [
        target.event_date
        for target in targets
    ] == [
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]


def test_clearing_is_limited_by_economic_receivable_capacity():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="50.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal(
        "50.00"
    )


def test_clearing_is_limited_by_commercial_settlement_capacity():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="40.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal(
        "40.00"
    )


def test_fifo_uses_source_id_as_same_date_tiebreaker():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=12,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="50.00",
                ),
                settlement(
                    source_id=11,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="70.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=22,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="50.00",
                ),
                receivable(
                    source_id=21,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="70.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert targets == (
        CustomerAdvanceClearingTarget(
            settlement_source_id=11,
            receivable_source_id=21,
            event_date=date(
                2026,
                9,
                2,
            ),
            amount=Decimal(
                "70.00"
            ),
            currency_code="UAH",
        ),
        CustomerAdvanceClearingTarget(
            settlement_source_id=12,
            receivable_source_id=22,
            event_date=date(
                2026,
                9,
                2,
            ),
            amount=Decimal(
                "50.00"
            ),
            currency_code="UAH",
        ),
    )


def test_one_settlement_can_split_across_multiple_receivables():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="100.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="30.00",
                ),
                receivable(
                    source_id=21,
                    event_date=date(
                        2026,
                        9,
                        3,
                    ),
                    amount="70.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert [
        (
            target.settlement_source_id,
            target.receivable_source_id,
            target.amount,
        )
        for target in targets
    ] == [
        (
            10,
            20,
            Decimal("30.00"),
        ),
        (
            10,
            21,
            Decimal("70.00"),
        ),
    ]


def test_gross_receivable_amount_is_not_reduced_by_vat_in_math():
    targets = (
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert targets[0].amount == Decimal(
        "120.00"
    )


def test_input_order_does_not_change_fifo_result():
    settlements = (
        settlement(
            source_id=10,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount="70.00",
        ),
        settlement(
            source_id=11,
            event_date=date(
                2026,
                9,
                2,
            ),
            amount="50.00",
        ),
    )

    receivables = (
        receivable(
            source_id=20,
            event_date=date(
                2026,
                9,
                1,
            ),
            amount="60.00",
        ),
        receivable(
            source_id=21,
            event_date=date(
                2026,
                9,
                3,
            ),
            amount="60.00",
        ),
    )

    forward = (
        build_customer_advance_clearing_targets(
            settlements=settlements,
            receivables=receivables,
            currency_code="UAH",
        )
    )

    reversed_input = (
        build_customer_advance_clearing_targets(
            settlements=tuple(
                reversed(
                    settlements
                )
            ),
            receivables=tuple(
                reversed(
                    receivables
                )
            ),
            currency_code="UAH",
        )
    )

    assert reversed_input == forward


def test_duplicate_settlement_source_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingSourceError
    ):
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="60.00",
                ),
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="60.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )


def test_duplicate_receivable_source_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingSourceError
    ):
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="60.00",
                ),
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        3,
                    ),
                    amount="60.00",
                ),
            ),
            currency_code="UAH",
        )


def test_currency_mismatch_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingCurrencyError
    ):
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                    currency_code="UAH",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="120.00",
                    currency_code="USD",
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "bad_amount",
    (
        "0",
        "-1",
    ),
)
def test_nonpositive_capacity_is_rejected(
    bad_amount: str,
):
    with pytest.raises(
        CustomerAdvanceClearingAmountError
    ):
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=10,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount=bad_amount,
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )


def test_nonpositive_source_id_is_rejected():
    with pytest.raises(
        CustomerAdvanceClearingSourceError
    ):
        build_customer_advance_clearing_targets(
            settlements=(
                settlement(
                    source_id=0,
                    event_date=date(
                        2026,
                        9,
                        1,
                    ),
                    amount="120.00",
                ),
            ),
            receivables=(
                receivable(
                    source_id=20,
                    event_date=date(
                        2026,
                        9,
                        2,
                    ),
                    amount="120.00",
                ),
            ),
            currency_code="UAH",
        )
