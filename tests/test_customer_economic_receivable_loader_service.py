from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.customer_economic_receivable_loader_service import (
    CustomerEconomicReceivableLoaderContextError,
    CustomerEconomicReceivableLoaderDataIntegrityError,
    build_active_customer_economic_receivable_candidates,
    load_customer_economic_receivable_candidates_for_invoice,
)


def event(
    *,
    event_id: int,
    company_id: int = 1,
    fulfillment_source_id: int,
    recognition_date: date,
    gross_amount: str,
    currency_code: str = "UAH",
    reversal_of_id: int | None = None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=company_id,
        invoice_fulfillment_allocation_id=(
            fulfillment_source_id
        ),
        recognition_date=recognition_date,
        recognized_gross_amount=Decimal(
            gross_amount
        ),
        currency_code=currency_code,
        reversal_of_id=reversal_of_id,
    )


def test_empty_history_returns_no_receivable_capacity():
    assert (
        build_active_customer_economic_receivable_candidates(
            events=(),
            company_id=1,
        )
        == ()
    )


def test_active_original_becomes_economic_receivable_candidate():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                ),
            ),
            company_id=1,
        )
    )

    assert len(result) == 1

    candidate = result[0]

    assert candidate.source_id == 10
    assert candidate.event_date == date(
        2026,
        9,
        2,
    )
    assert candidate.amount == Decimal(
        "120.00"
    )
    assert candidate.currency_code == "UAH"


def test_reversed_original_is_not_active_capacity():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        3,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=10,
                ),
            ),
            company_id=1,
        )
    )

    assert result == ()


def test_replacement_original_is_active_after_old_original_reversed():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=10,
                ),
                event(
                    event_id=12,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="100.00",
                ),
            ),
            company_id=1,
        )
    )

    assert [
        candidate.source_id
        for candidate in result
    ] == [
        12
    ]

    assert result[0].amount == Decimal(
        "100.00"
    )


def test_multiple_active_sources_are_sorted_fifo():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=30,
                    fulfillment_source_id=300,
                    recognition_date=date(
                        2026,
                        9,
                        3,
                    ),
                    gross_amount="30.00",
                ),
                event(
                    event_id=20,
                    fulfillment_source_id=200,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="20.00",
                ),
                event(
                    event_id=19,
                    fulfillment_source_id=190,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="19.00",
                ),
            ),
            company_id=1,
        )
    )

    assert [
        candidate.source_id
        for candidate in result
    ] == [
        19,
        20,
        30,
    ]


def test_loader_uses_full_gross_amount_including_vat():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                ),
            ),
            company_id=1,
        )
    )

    assert result[0].amount == Decimal(
        "120.00"
    )


def test_currency_is_normalized():
    result = (
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120",
                    currency_code="uah",
                ),
            ),
            company_id=1,
        )
    )

    assert result[0].currency_code == "UAH"
    assert result[0].amount == Decimal(
        "120.00"
    )


def test_company_mismatch_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    company_id=2,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                ),
            ),
            company_id=1,
        )


def test_duplicate_event_id_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="60.00",
                ),
                event(
                    event_id=10,
                    fulfillment_source_id=101,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="60.00",
                ),
            ),
            company_id=1,
        )


def test_reversal_without_loaded_original_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        3,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=10,
                ),
            ),
            company_id=1,
        )


def test_reversal_cannot_target_another_reversal():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=10,
                ),
                event(
                    event_id=12,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        3,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=11,
                ),
            ),
            company_id=1,
        )


def test_reversal_source_mismatch_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=101,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                    reversal_of_id=10,
                ),
            ),
            company_id=1,
        )


def test_reversal_currency_mismatch_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                    currency_code="UAH",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="120.00",
                    currency_code="USD",
                    reversal_of_id=10,
                ),
            ),
            company_id=1,
        )


def test_reversal_gross_mismatch_is_rejected():
    with pytest.raises(
        CustomerEconomicReceivableLoaderDataIntegrityError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(
                event(
                    event_id=10,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        1,
                    ),
                    gross_amount="120.00",
                ),
                event(
                    event_id=11,
                    fulfillment_source_id=100,
                    recognition_date=date(
                        2026,
                        9,
                        2,
                    ),
                    gross_amount="119.00",
                    reversal_of_id=10,
                ),
            ),
            company_id=1,
        )


@pytest.mark.parametrize(
    "bad_value",
    (
        0,
        -1,
        True,
    ),
)
def test_invalid_loader_context_is_rejected(
    bad_value,
):
    with pytest.raises(
        CustomerEconomicReceivableLoaderContextError
    ):
        build_active_customer_economic_receivable_candidates(
            events=(),
            company_id=bad_value,
        )


class FakeScalarResult:
    def __init__(
        self,
        events,
    ):
        self._events = list(
            events
        )

    def scalars(
        self,
    ):
        return self

    def all(
        self,
    ):
        return list(
            self._events
        )


class FakeDb:
    def __init__(
        self,
        rows,
        *,
        sales_return_rows=(),
    ):
        # Existing first-query fixture:
        # immutable SalesRecognitionEvent history.
        self.rows = tuple(
            rows
        )

        # New second-query fixture:
        # immutable SalesReturnRecognitionEvent history.
        #
        # Legacy tests predate Sales Return and therefore
        # correctly default to no return history.
        self.sales_return_rows = tuple(
            sales_return_rows
        )

        # Preserve the legacy single-statement inspection
        # contract: this remains the FIRST loader query.
        self.statement = None

        # Also retain the complete execution sequence for
        # the new two-query loader contract.
        self.executed_statements = []

    async def execute(
        self,
        statement,
    ):
        self.executed_statements.append(
            statement
        )

        if self.statement is None:
            self.statement = statement

        query_number = len(
            self.executed_statements
        )

        if query_number == 1:
            rows = self.rows

        elif query_number == 2:
            rows = self.sales_return_rows

        else:
            raise AssertionError(
                "Unexpected database query number "
                f"{query_number} in customer economic "
                "receivable loader unit fixture"
            )

        return FakeScalarResult(
            rows
        )


@pytest.mark.asyncio
async def test_async_loader_queries_invoice_history_and_returns_active_capacity():
    db = FakeDb(
        (
            event(
                event_id=10,
                fulfillment_source_id=100,
                recognition_date=date(
                    2026,
                    9,
                    1,
                ),
                gross_amount="120.00",
            ),
        )
    )

    result = (
        await load_customer_economic_receivable_candidates_for_invoice(
            db,
            company_id=1,
            invoice_id=500,
        )
    )

    assert [
        candidate.source_id
        for candidate in result
    ] == [
        10
    ]

    assert result[0].amount == Decimal(
        "120.00"
    )

    assert db.statement is not None

    sql = str(
        db.statement.compile(
            compile_kwargs={
                "literal_binds": True,
            }
        )
    )

    assert (
        "sales_recognition_events"
        in sql
    )

    assert (
        "invoice_fulfillment_allocations"
        in sql
    )

    assert (
        "invoice_fulfillment_allocations.invoice_id"
        in sql
    )

    assert "500" in sql


@pytest.mark.asyncio
async def test_async_loader_keeps_reversal_history_for_active_reconstruction():
    db = FakeDb(
        (
            event(
                event_id=10,
                fulfillment_source_id=100,
                recognition_date=date(
                    2026,
                    9,
                    1,
                ),
                gross_amount="120.00",
            ),
            event(
                event_id=11,
                fulfillment_source_id=100,
                recognition_date=date(
                    2026,
                    9,
                    3,
                ),
                gross_amount="120.00",
                reversal_of_id=10,
            ),
        )
    )

    result = (
        await load_customer_economic_receivable_candidates_for_invoice(
            db,
            company_id=1,
            invoice_id=500,
        )
    )

    assert result == ()

    sql = str(
        db.statement.compile(
            compile_kwargs={
                "literal_binds": True,
            }
        )
    )

    assert (
        "sales_recognition_events.reversal_of_id IS NULL"
        not in sql
    )
