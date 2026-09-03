from datetime import date
from decimal import Decimal

import pytest

from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeCandidate,
    InputVatFulfillmentBridgeDataIntegrityError,
    build_input_vat_fulfillment_bridge_targets,
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


def candidate(
    source_id: int,
    quantity: str,
    event_date: date = D1,
) -> InputVatFulfillmentBridgeCandidate:
    return InputVatFulfillmentBridgeCandidate(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
    )


def build(
    *,
    invoice_quantity: str = "10",
    tax_amount: str = "20.00",
    candidates=(),
):
    return build_input_vat_fulfillment_bridge_targets(
        tax_calculation_id=101,
        invoice_line_quantity=Decimal(
            invoice_quantity
        ),
        tax_amount=Decimal(
            tax_amount
        ),
        currency_code="UAH",
        candidates=candidates,
    )


def test_full_single_allocation_gets_full_tax():
    targets = build(
        candidates=(
            candidate(
                1,
                "10",
            ),
        )
    )

    assert len(
        targets
    ) == 1

    assert (
        targets[0].amount
        == Decimal("20.00")
    )

    assert (
        targets[0].tax_calculation_id
        == 101
    )

    assert (
        targets[0].source_id
        == 1
    )

    assert (
        targets[0].event_date
        == D1
    )

    assert (
        targets[0].currency_code
        == "UAH"
    )


def test_partial_allocation_is_proportional():
    targets = build(
        candidates=(
            candidate(
                1,
                "3",
            ),
        )
    )

    assert (
        targets[0].amount
        == Decimal("6.00")
    )


def test_multiple_allocations_reconcile_to_full_tax():
    targets = build(
        candidates=(
            candidate(
                1,
                "3",
            ),
            candidate(
                2,
                "7",
                D2,
            ),
        )
    )

    assert [
        target.amount
        for target in targets
    ] == [
        Decimal("6.00"),
        Decimal("14.00"),
    ]

    assert sum(
        target.amount
        for target in targets
    ) == Decimal("20.00")


def test_cumulative_delta_prevents_rounding_overstatement():
    targets = build(
        invoice_quantity="3",
        tax_amount="0.02",
        candidates=(
            candidate(
                1,
                "1",
            ),
            candidate(
                2,
                "1",
            ),
            candidate(
                3,
                "1",
            ),
        ),
    )

    # Independent per-source rounding would produce:
    #
    #   0.02 / 3 -> 0.01
    #   0.01 * 3 -> 0.03
    #
    # Cumulative-delta rounding must preserve the immutable
    # TaxCalculation total of 0.02.
    assert [
        target.amount
        for target in targets
    ] == [
        Decimal("0.01"),
        Decimal("0.00"),
        Decimal("0.01"),
    ]

    assert sum(
        target.amount
        for target in targets
    ) == Decimal("0.02")


def test_zero_amount_target_is_retained_for_reconciliation():
    targets = build(
        invoice_quantity="3",
        tax_amount="0.01",
        candidates=(
            candidate(
                1,
                "1",
            ),
            candidate(
                2,
                "1",
            ),
            candidate(
                3,
                "1",
            ),
        ),
    )

    assert [
        target.amount
        for target in targets
    ] == [
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("0.00"),
    ]

    assert (
        targets[0].is_zero
        is True
    )

    assert (
        targets[1].is_zero
        is False
    )


def test_candidates_are_ordered_by_date_then_source():
    targets = build(
        candidates=(
            candidate(
                30,
                "2",
                D2,
            ),
            candidate(
                20,
                "3",
                D1,
            ),
            candidate(
                10,
                "1",
                D1,
            ),
        )
    )

    assert [
        target.source_id
        for target in targets
    ] == [
        10,
        20,
        30,
    ]

    assert [
        target.event_date
        for target in targets
    ] == [
        D1,
        D1,
        D2,
    ]


def test_partial_total_is_aggregate_capped():
    targets = build(
        candidates=(
            candidate(
                1,
                "2",
            ),
            candidate(
                2,
                "2",
            ),
        )
    )

    assert sum(
        target.amount
        for target in targets
    ) == Decimal("8.00")


def test_zero_tax_produces_zero_targets():
    targets = build(
        tax_amount="0.00",
        candidates=(
            candidate(
                1,
                "4",
            ),
            candidate(
                2,
                "6",
            ),
        ),
    )

    assert [
        target.amount
        for target in targets
    ] == [
        Decimal("0.00"),
        Decimal("0.00"),
    ]


def test_empty_candidates_return_empty_targets():
    assert build(
        candidates=()
    ) == ()


def test_duplicate_source_id_is_rejected():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="Duplicate",
    ):
        build(
            candidates=(
                candidate(
                    1,
                    "2",
                ),
                candidate(
                    1,
                    "3",
                ),
            )
        )


def test_total_active_quantity_cannot_exceed_invoice_quantity():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="exceeds invoice line",
    ):
        build(
            candidates=(
                candidate(
                    1,
                    "6",
                ),
                candidate(
                    2,
                    "5",
                ),
            )
        )


@pytest.mark.parametrize(
    "quantity",
    [
        "0",
        "-1",
    ],
)
def test_candidate_quantity_must_be_positive(
    quantity,
):
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="candidate quantity",
    ):
        build(
            candidates=(
                candidate(
                    1,
                    quantity,
                ),
            )
        )


@pytest.mark.parametrize(
    "invoice_quantity",
    [
        "0",
        "-1",
    ],
)
def test_invoice_quantity_must_be_positive(
    invoice_quantity,
):
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="invoice_line_quantity",
    ):
        build(
            invoice_quantity=invoice_quantity,
            candidates=(),
        )


def test_negative_tax_is_rejected():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="tax_amount cannot be negative",
    ):
        build(
            tax_amount="-0.01",
            candidates=(),
        )


def test_tax_calculation_id_must_be_positive():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="tax_calculation_id",
    ):
        build_input_vat_fulfillment_bridge_targets(
            tax_calculation_id=0,
            invoice_line_quantity=Decimal("10"),
            tax_amount=Decimal("20"),
            currency_code="UAH",
            candidates=(),
        )


def test_source_id_must_be_positive():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="source_id",
    ):
        build(
            candidates=(
                candidate(
                    0,
                    "1",
                ),
            )
        )


@pytest.mark.parametrize(
    "currency_code",
    [
        "",
        "UA",
        "UAHH",
    ],
)
def test_currency_code_must_have_three_characters(
    currency_code,
):
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match="currency_code",
    ):
        build_input_vat_fulfillment_bridge_targets(
            tax_calculation_id=1,
            invoice_line_quantity=Decimal("10"),
            tax_amount=Decimal("20"),
            currency_code=currency_code,
            candidates=(),
        )


def test_wrong_candidate_type_is_rejected():
    with pytest.raises(
        InputVatFulfillmentBridgeDataIntegrityError,
        match=(
            "candidate must be "
            "InputVatFulfillmentBridgeCandidate"
        ),
    ):
        build(
            candidates=(
                object(),
            )
        )
