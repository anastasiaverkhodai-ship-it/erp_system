from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.customer_economic_receivable_loader_service import (
    CustomerEconomicReceivableLoaderDataIntegrityError,
    build_active_customer_economic_receivable_candidates,
)


def sales_event(
    *,
    event_id=1,
    amount="120.00",
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        invoice_fulfillment_allocation_id=10,
        recognition_date=date(
            2026,
            9,
            1,
        ),
        recognized_quantity=Decimal(
            "2.0000"
        ),
        recognized_gross_amount=Decimal(
            amount
        ),
        recognized_tax_amount=Decimal(
            "0.00"
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def return_event(
    *,
    event_id,
    amount,
    trade_return_event_id=100,
    sales_recognition_event_id=1,
    recognition_date=date(
        2026,
        9,
        5,
    ),
    reversal_of_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        trade_return_event_id=(
            trade_return_event_id
        ),
        sales_recognition_event_id=(
            sales_recognition_event_id
        ),
        recognition_date=recognition_date,
        returned_quantity=Decimal(
            "1.0000"
        ),
        returned_gross_amount=Decimal(
            amount
        ),
        returned_tax_amount=Decimal(
            "0.00"
        ),
        currency_code="UAH",
        reversal_of_id=reversal_of_id,
    )


def test_no_return_preserves_full_receivable_capacity():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            company_id=1,
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[
            0
        ].amount
        == Decimal(
            "120.00"
        )
    )


def test_active_sales_return_reduces_361_capacity():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                return_event(
                    event_id=101,
                    amount="60.00",
                ),
            ),
            company_id=1,
        )
    )

    assert len(
        result
    ) == 1

    assert (
        result[
            0
        ].source_id
        == 1
    )

    assert (
        result[
            0
        ].amount
        == Decimal(
            "60.00"
        )
    )


def test_multiple_active_returns_reduce_same_source():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                return_event(
                    event_id=101,
                    amount="40.00",
                    trade_return_event_id=100,
                ),
                return_event(
                    event_id=102,
                    amount="30.00",
                    trade_return_event_id=101,
                ),
            ),
            company_id=1,
        )
    )

    assert (
        result[
            0
        ].amount
        == Decimal(
            "50.00"
        )
    )


def test_full_return_removes_receivable_candidate():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                return_event(
                    event_id=101,
                    amount="120.00",
                ),
            ),
            company_id=1,
        )
    )

    assert result == ()


def test_return_reversal_restores_full_361_capacity():
    original = return_event(
        event_id=101,
        amount="60.00",
    )

    reversal = return_event(
        event_id=102,
        amount="60.00",
        reversal_of_id=101,
    )

    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                original,
                reversal,
            ),
            company_id=1,
        )
    )

    assert (
        result[
            0
        ].amount
        == Decimal(
            "120.00"
        )
    )


def test_return_replacement_after_reversal_is_current_capacity():
    original = return_event(
        event_id=101,
        amount="60.00",
    )

    reversal = return_event(
        event_id=102,
        amount="60.00",
        reversal_of_id=101,
    )

    replacement = return_event(
        event_id=103,
        amount="30.00",
        trade_return_event_id=100,
    )

    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                original,
                reversal,
                replacement,
            ),
            company_id=1,
        )
    )

    assert (
        result[
            0
        ].amount
        == Decimal(
            "90.00"
        )
    )


def test_active_return_cannot_exceed_receivable_capacity():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError,
        match="exceeds",
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                sales_event(),
            ),
            sales_return_events=(
                return_event(
                    event_id=101,
                    amount="121.00",
                ),
            ),
            company_id=1,
        )


def test_active_return_cannot_reference_inactive_sales_source():
    original_sales = sales_event(
        event_id=1,
    )

    reversal_sales = sales_event(
        event_id=2,
        reversal_of_id=1,
    )

    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError,
        match="inactive",
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                original_sales,
                reversal_sales,
            ),
            sales_return_events=(
                return_event(
                    event_id=101,
                    amount="60.00",
                    sales_recognition_event_id=1,
                ),
            ),
            company_id=1,
        )
