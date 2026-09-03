from datetime import date
from decimal import Decimal

import pytest

from app.services.supplier_economic_liability_calculation_service import (
    SupplierEconomicLiabilityAmountError,
    SupplierEconomicLiabilityCurrencyError,
    SupplierEconomicLiabilitySourceError,
    SupplierReceiptBaseAllocationCandidate,
    SupplierReceiptBaseAllocationTarget,
    SupplierVatLiabilityComponent,
    build_supplier_economic_liability_candidates,
    build_supplier_receipt_base_allocation_targets,
)


D1 = date(
    2026,
    8,
    10,
)

D2 = date(
    2026,
    8,
    20,
)


def allocation(
    source_id: int,
    quantity: str,
    *,
    event_date=D1,
):
    return SupplierReceiptBaseAllocationCandidate(
        source_id=source_id,
        event_date=event_date,
        quantity=Decimal(
            quantity
        ),
    )


def base_target(
    source_id: int,
    amount: str,
    *,
    event_date=D1,
):
    return SupplierReceiptBaseAllocationTarget(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
        currency_code="UAH",
    )


def vat(
    source_id: int,
    amount: str,
    *,
    event_date=D1,
):
    return SupplierVatLiabilityComponent(
        source_id=source_id,
        event_date=event_date,
        amount=Decimal(
            amount
        ),
    )


def test_full_receipt_base_allocation_closes_exactly():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
            ),
        )
    )

    assert len(targets) == 1
    assert (
        targets[0].amount
        == Decimal("100.00")
    )


def test_three_partial_allocations_use_cumulative_delta():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
                allocation(
                    3,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
        Decimal("83.33"),
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal("250.00")


def test_partial_allocation_is_proportionally_capped():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
    )

    assert sum(
        (
            target.amount
            for target in targets
        ),
        Decimal("0.00"),
    ) == Decimal("166.67")


def test_unsorted_allocations_are_deterministic():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="uah",
            candidates=(
                allocation(
                    3,
                    "1",
                ),
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
            ),
        )
    )

    assert tuple(
        target.source_id
        for target in targets
    ) == (
        1,
        2,
        3,
    )

    assert tuple(
        target.amount
        for target in targets
    ) == (
        Decimal("83.33"),
        Decimal("83.34"),
        Decimal("83.33"),
    )


def test_allocation_event_date_is_preserved():
    targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                    event_date=D2,
                ),
            ),
        )
    )

    assert (
        targets[0].event_date
        == D2
    )


def test_allocation_over_receipt_quantity_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilityAmountError,
        match="exceeds",
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "0.60",
                ),
                allocation(
                    2,
                    "0.50",
                ),
            ),
        )


def test_duplicate_allocation_source_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="unique",
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "0.50",
                ),
                allocation(
                    1,
                    "0.50",
                ),
            ),
        )


@pytest.mark.parametrize(
    "quantity",
    (
        "0",
        "-1",
    ),
)
def test_nonpositive_allocation_quantity_is_rejected(
    quantity,
):
    with pytest.raises(
        SupplierEconomicLiabilityAmountError
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    quantity,
                ),
            ),
        )


def test_non_vat_liability_equals_base():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(),
            currency_code="UAH",
        )
    )

    assert len(candidates) == 1

    assert (
        candidates[0].amount
        == Decimal("100.00")
    )


def test_vat_liability_equals_base_plus_bridge():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "20.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        candidates[0].amount
        == Decimal("120.00")
    )


def test_multiple_vat_components_are_summed():
    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "12.00",
                ),
                vat(
                    1,
                    "8.00",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert (
        candidates[0].amount
        == Decimal("120.00")
    )


def test_three_inclusive_allocations_close_to_gross_300():
    base_targets = (
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("3"),
            receipt_base_amount=Decimal("250.00"),
            currency_code="UAH",
            candidates=(
                allocation(
                    1,
                    "1",
                ),
                allocation(
                    2,
                    "1",
                ),
                allocation(
                    3,
                    "1",
                ),
            ),
        )
    )

    candidates = (
        build_supplier_economic_liability_candidates(
            base_targets=base_targets,
            vat_components=(
                vat(
                    1,
                    "16.67",
                ),
                vat(
                    2,
                    "16.66",
                ),
                vat(
                    3,
                    "16.67",
                ),
            ),
            currency_code="UAH",
        )
    )

    assert tuple(
        candidate.amount
        for candidate in candidates
    ) == (
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    )

    assert sum(
        (
            candidate.amount
            for candidate in candidates
        ),
        Decimal("0.00"),
    ) == Decimal("300.00")


def test_vat_component_without_base_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="no matching",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    2,
                    "20.00",
                ),
            ),
            currency_code="UAH",
        )


def test_vat_date_must_match_receipt_date():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="event_date",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                    event_date=D1,
                ),
            ),
            vat_components=(
                vat(
                    1,
                    "20.00",
                    event_date=D2,
                ),
            ),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "amount",
    (
        "0",
        "-1",
    ),
)
def test_nonpositive_vat_component_is_rejected(
    amount,
):
    with pytest.raises(
        SupplierEconomicLiabilityAmountError
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "100.00",
                ),
            ),
            vat_components=(
                vat(
                    1,
                    amount,
                ),
            ),
            currency_code="UAH",
        )


def test_duplicate_base_source_is_rejected():
    with pytest.raises(
        SupplierEconomicLiabilitySourceError,
        match="unique",
    ):
        build_supplier_economic_liability_candidates(
            base_targets=(
                base_target(
                    1,
                    "50.00",
                ),
                base_target(
                    1,
                    "50.00",
                ),
            ),
            vat_components=(),
            currency_code="UAH",
        )


@pytest.mark.parametrize(
    "currency",
    (
        "",
        "UA",
        "UAHH",
        "1AH",
    ),
)
def test_invalid_currency_is_rejected(
    currency,
):
    with pytest.raises(
        SupplierEconomicLiabilityCurrencyError
    ):
        build_supplier_receipt_base_allocation_targets(
            receipt_quantity=Decimal("1"),
            receipt_base_amount=Decimal("100"),
            currency_code=currency,
            candidates=(
                allocation(
                    1,
                    "1",
                ),
            ),
        )
