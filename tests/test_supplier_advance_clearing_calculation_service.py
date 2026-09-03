from datetime import date
from decimal import Decimal

import pytest

from app.services.supplier_advance_clearing_calculation_service import (
    SupplierAdvanceClearingAmountError,
    SupplierAdvanceClearingCurrencyError,
    SupplierAdvanceClearingSourceError,
    SupplierAdvanceSettlementCandidate,
    SupplierEconomicLiabilityCandidate,
    build_supplier_advance_clearing_targets,
)


def settlement(
    source_id: int,
    day: int,
    amount: str,
):
    return SupplierAdvanceSettlementCandidate(
        source_id=source_id,
        event_date=date(
            2026,
            8,
            day,
        ),
        amount=Decimal(
            amount
        ),
    )


def liability(
    source_id: int,
    day: int,
    amount: str,
):
    return SupplierEconomicLiabilityCandidate(
        source_id=source_id,
        event_date=date(
            2026,
            8,
            day,
        ),
        amount=Decimal(
            amount
        ),
    )


def test_payment_first_waits_for_liability_date():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    10,
                    "120.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert len(targets) == 1

    assert (
        targets[0].amount
        == Decimal("120.00")
    )

    assert (
        targets[0].event_date
        == date(
            2026,
            8,
            20,
        )
    )


def test_receipt_first_waits_for_settlement_date():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    20,
                    "120.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    10,
                    "120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert len(targets) == 1

    assert (
        targets[0].event_date
        == date(
            2026,
            8,
            20,
        )
    )


def test_one_settlement_can_clear_in_two_receipt_tranches():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    10,
                    "120.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "60.00",
                ),
                liability(
                    12,
                    25,
                    "60.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("60.00"),
        Decimal("60.00"),
    )

    assert tuple(
        target.event_date
        for target in targets
    ) == (
        date(
            2026,
            8,
            20,
        ),
        date(
            2026,
            8,
            25,
        ),
    )

    assert tuple(
        target.settlement_source_id
        for target in targets
    ) == (
        1,
        1,
    )

    assert tuple(
        target.liability_source_id
        for target in targets
    ) == (
        11,
        12,
    )


def test_one_liability_can_clear_two_settlements():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    20,
                    "50.00",
                ),
                settlement(
                    2,
                    25,
                    "70.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    10,
                    "120.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("50.00"),
        Decimal("70.00"),
    )

    assert tuple(
        target.event_date
        for target in targets
    ) == (
        date(
            2026,
            8,
            20,
        ),
        date(
            2026,
            8,
            25,
        ),
    )


def test_settlement_capacity_can_exceed_liability():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    10,
                    "120.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "60.00",
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
    ) == Decimal("60.00")


def test_liability_capacity_can_exceed_settlement():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    20,
                    "60.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    10,
                    "120.00",
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
    ) == Decimal("60.00")


def test_matching_is_deterministic_for_unsorted_inputs():
    targets = (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    2,
                    15,
                    "70.00",
                ),
                settlement(
                    1,
                    10,
                    "50.00",
                ),
            ),
            liabilities=(
                liability(
                    12,
                    25,
                    "70.00",
                ),
                liability(
                    11,
                    20,
                    "50.00",
                ),
            ),
            currency_code="uah",
        )
    )

    assert tuple(
        (
            target.settlement_source_id,
            target.liability_source_id,
            target.event_date,
            target.amount,
            target.currency_code,
        )
        for target in targets
    ) == (
        (
            1,
            11,
            date(
                2026,
                8,
                20,
            ),
            Decimal("50.00"),
            "UAH",
        ),
        (
            2,
            12,
            date(
                2026,
                8,
                25,
            ),
            Decimal("70.00"),
            "UAH",
        ),
    )


def test_empty_side_produces_no_targets():
    assert (
        build_supplier_advance_clearing_targets(
            settlements=(),
            liabilities=(
                liability(
                    11,
                    20,
                    "120.00",
                ),
            ),
            currency_code="UAH",
        )
        == ()
    )

    assert (
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    10,
                    "120.00",
                ),
            ),
            liabilities=(),
            currency_code="UAH",
        )
        == ()
    )


@pytest.mark.parametrize(
    "settlements, liabilities",
    (
        (
            (
                settlement(
                    1,
                    10,
                    "50.00",
                ),
                settlement(
                    1,
                    11,
                    "50.00",
                ),
            ),
            (),
        ),
        (
            (),
            (
                liability(
                    11,
                    10,
                    "50.00",
                ),
                liability(
                    11,
                    11,
                    "50.00",
                ),
            ),
        ),
    ),
)
def test_duplicate_source_ids_are_rejected(
    settlements,
    liabilities,
):
    with pytest.raises(
        SupplierAdvanceClearingSourceError
    ):
        build_supplier_advance_clearing_targets(
            settlements=settlements,
            liabilities=liabilities,
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "candidate",
    (
        settlement(
            1,
            10,
            "0.00",
        ),
        settlement(
            1,
            10,
            "-1.00",
        ),
    ),
)
def test_nonpositive_settlement_amount_is_rejected(
    candidate,
):
    with pytest.raises(
        SupplierAdvanceClearingAmountError
    ):
        build_supplier_advance_clearing_targets(
            settlements=(
                candidate,
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "100.00",
                ),
            ),
            currency_code="UAH",
        )


def test_invalid_source_id_is_rejected():
    with pytest.raises(
        SupplierAdvanceClearingSourceError
    ):
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    0,
                    10,
                    "100.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "100.00",
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "currency_code",
    (
        "",
        "UA",
        "UAHH",
        "12A",
    ),
)
def test_invalid_currency_code_is_rejected(
    currency_code,
):
    with pytest.raises(
        SupplierAdvanceClearingCurrencyError
    ):
        build_supplier_advance_clearing_targets(
            settlements=(
                settlement(
                    1,
                    10,
                    "100.00",
                ),
            ),
            liabilities=(
                liability(
                    11,
                    20,
                    "100.00",
                ),
            ),
            currency_code=currency_code,
        )
