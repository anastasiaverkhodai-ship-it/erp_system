import inspect
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_return_input_vat_credit_correction_persistence_service as service
from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.services.purchase_return_input_vat_credit_correction_calculation_service import (
    PurchaseReturnInputVatCreditCorrectionTarget,
)
from app.services.purchase_return_input_vat_credit_correction_persistence_service import (
    PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError,
    build_purchase_return_input_vat_credit_correction_source_plan,
    reconcile_purchase_return_input_vat_credit_correction_source,
)


D1 = date(
    2026,
    9,
    5,
)

D2 = date(
    2026,
    9,
    6,
)


def target(
    *,
    base="25.00",
    tax="5.00",
    adjustment_date=D1,
):
    return (
        PurchaseReturnInputVatCreditCorrectionTarget(
            purchase_return_vat_adjustment_event_id=10,
            tax_calculation_id=20,
            adjustment_date=adjustment_date,
            reduced_taxable_base=Decimal(
                base
            ),
            reduced_tax_amount=Decimal(
                tax
            ),
            currency_code="UAH",
        )
    )


def event(
    event_id,
    *,
    base="25.00",
    tax="5.00",
    adjustment_date=D1,
    reversal_of_id=None,
):
    return (
        PurchaseReturnInputVatCreditCorrectionEvent(
            id=event_id,
            company_id=1,
            purchase_return_vat_adjustment_event_id=10,
            tax_calculation_id=20,
            adjustment_date=adjustment_date,
            reduced_taxable_base=Decimal(
                base
            ),
            reduced_tax_amount=Decimal(
                tax
            ),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def test_no_history_positive_creates_original():
    desired = target()

    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=desired,
            events=(),
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target is desired


def test_exact_active_is_noop():
    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=target(),
            events=(
                event(
                    1
                ),
            ),
        )
    )

    assert plan.is_noop is True


def test_changed_positive_reverses_and_replaces():
    desired = target(
        base="15.00",
        tax="3.00",
        adjustment_date=D2,
    )

    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=desired,
            events=(
                event(
                    1
                ),
            ),
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )

    assert (
        plan.replacement_target
        is desired
    )


def test_zero_target_reverses_active_only():
    desired = target(
        base="0.00",
        tax="0.00",
        adjustment_date=D2,
    )

    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=desired,
            events=(
                event(
                    1
                ),
            ),
        )
    )

    assert plan.reversal_event_ids == (
        1,
    )

    assert (
        plan.replacement_target
        is None
    )


def test_zero_target_without_active_is_noop():
    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=target(
                base="0.00",
                tax="0.00",
            ),
            events=(),
        )
    )

    assert plan.is_noop is True


def test_fully_reversed_history_can_create_new_original():
    original = event(
        1
    )

    reversal = event(
        2,
        adjustment_date=D2,
        reversal_of_id=1,
    )

    desired = target(
        base="15.00",
        tax="3.00",
        adjustment_date=D2,
    )

    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=desired,
            events=(
                original,
                reversal,
            ),
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target is desired


def test_reversal_must_copy_historical_amounts():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError
    ):
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=target(),
            events=(
                event(
                    1
                ),
                event(
                    2,
                    tax="4.99",
                    adjustment_date=D2,
                    reversal_of_id=1,
                ),
            ),
        )


def test_reversal_of_reversal_fails():
    original = event(
        1
    )

    reversal = event(
        2,
        adjustment_date=D2,
        reversal_of_id=1,
    )

    second = event(
        3,
        adjustment_date=D2,
        reversal_of_id=2,
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError
    ):
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=target(),
            events=(
                original,
                reversal,
                second,
            ),
        )


def test_multiple_active_originals_fail():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError
    ):
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=1,
            target=target(),
            events=(
                event(
                    1
                ),
                event(
                    2
                ),
            ),
        )


class _ScalarResult:
    def __init__(
        self,
        rows,
    ):
        self.rows = rows

    def all(
        self,
    ):
        return list(
            self.rows
        )


class _ExecuteResult:
    def __init__(
        self,
        rows,
    ):
        self.rows = rows

    def scalars(
        self,
    ):
        return _ScalarResult(
            self.rows
        )


class _Session:
    def __init__(
        self,
        rows=(),
    ):
        self.rows = tuple(
            rows
        )
        self.added = []
        self.flush_count = 0

    async def execute(
        self,
        statement,
    ):
        return _ExecuteResult(
            self.rows
        )

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

    async def flush(
        self,
    ):
        self.flush_count += 1


@pytest.mark.asyncio
async def test_async_positive_creates_original():
    db = _Session()

    created = (
        await reconcile_purchase_return_input_vat_credit_correction_source(
            db,
            company_id=1,
            target=target(),
            created_by=9,
        )
    )

    assert len(
        created
    ) == 1

    assert (
        created[
            0
        ].reversal_of_id
        is None
    )

    assert (
        created[
            0
        ].reduced_tax_amount
        == Decimal("5.00")
    )

    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_async_changed_creates_reversal_then_replacement():
    original = event(
        1
    )

    db = _Session(
        rows=(
            original,
        )
    )

    created = (
        await reconcile_purchase_return_input_vat_credit_correction_source(
            db,
            company_id=1,
            target=target(
                base="15.00",
                tax="3.00",
                adjustment_date=D2,
            ),
            created_by=9,
            reversal_date=D2,
        )
    )

    assert len(
        created
    ) == 2

    reversal = created[
        0
    ]

    replacement = created[
        1
    ]

    assert (
        reversal.reversal_of_id
        == 1
    )

    assert (
        reversal.reduced_tax_amount
        == Decimal("5.00")
    )

    assert (
        replacement.reversal_of_id
        is None
    )

    assert (
        replacement.reduced_tax_amount
        == Decimal("3.00")
    )


def test_service_contains_no_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
