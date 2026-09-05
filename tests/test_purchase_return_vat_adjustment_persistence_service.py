import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.services.purchase_return_vat_adjustment_calculation_service import (
    build_purchase_return_vat_adjustment_target,
)
from app.services.purchase_return_vat_adjustment_persistence_service import (
    PurchaseReturnVatAdjustmentPersistenceDataIntegrityError,
    build_purchase_return_vat_adjustment_source_plan,
    reconcile_purchase_return_vat_adjustment_source,
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
    adjustment_date=D1,
    base="100.00",
    tax="20.00",
    basis_kind="goods_received_by_supplier",
):
    return (
        build_purchase_return_vat_adjustment_target(
            purchase_return_recognition_event_id=10,
            tax_calculation_id=20,
            adjustment_date=adjustment_date,
            basis_kind=basis_kind,
            adjusted_taxable_base=Decimal(base),
            adjusted_tax_amount=Decimal(tax),
            currency_code="UAH",
        )
    )


def event(
    event_id,
    *,
    adjustment_date=D1,
    base="100.00",
    tax="20.00",
    reversal_of_id=None,
    basis_kind="goods_received_by_supplier",
):
    return (
        PurchaseReturnVatAdjustmentEvent(
            id=event_id,
            company_id=1,
            purchase_return_recognition_event_id=10,
            tax_calculation_id=20,
            adjustment_date=adjustment_date,
            basis_kind=basis_kind,
            adjusted_taxable_base=Decimal(base),
            adjusted_tax_amount=Decimal(tax),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def test_new_positive_target_creates_original():
    desired = target()

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == desired


def test_exact_active_target_is_noop():
    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=target(),
        )
    )

    assert plan.is_noop is True


def test_changed_positive_target_reverses_and_replaces():
    desired = target(
        base="60.00",
        tax="12.00",
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == (1,)
    assert plan.replacement_target == desired


def test_date_change_reverses_and_replaces():
    desired = target(
        adjustment_date=D2
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == (1,)
    assert plan.replacement_target == desired


def test_zero_target_reverses_without_replacement():
    desired = target(
        base="0.00",
        tax="0.00",
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(
                event(
                    1
                ),
            ),
            target=desired,
        )
    )

    assert plan.reversal_event_ids == (1,)
    assert plan.replacement_target is None


def test_zero_target_without_active_original_is_noop():
    desired = target(
        base="0.00",
        tax="0.00",
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=(),
            target=desired,
        )
    )

    assert plan.is_noop is True


def test_reversed_history_allows_new_replacement():
    history = (
        event(
            1
        ),
        event(
            2,
            adjustment_date=D2,
            reversal_of_id=1,
        ),
    )

    desired = target(
        adjustment_date=D2,
        base="40.00",
        tax="8.00",
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=history,
            target=desired,
        )
    )

    assert plan.reversal_event_ids == ()
    assert plan.replacement_target == desired


def test_multiple_active_originals_fail_closed():
    with pytest.raises(
        PurchaseReturnVatAdjustmentPersistenceDataIntegrityError
    ):
        build_purchase_return_vat_adjustment_source_plan(
            events=(
                event(
                    1
                ),
                event(
                    2
                ),
            ),
            target=target(),
        )


def test_reversal_of_reversal_fails_closed():
    history = (
        event(
            1
        ),
        event(
            2,
            adjustment_date=D2,
            reversal_of_id=1,
        ),
        event(
            3,
            adjustment_date=D2,
            reversal_of_id=2,
        ),
    )

    with pytest.raises(
        PurchaseReturnVatAdjustmentPersistenceDataIntegrityError
    ):
        build_purchase_return_vat_adjustment_source_plan(
            events=history,
            target=target(
                base="40.00",
                tax="8.00",
            ),
        )


class _ScalarResult:
    def __init__(
        self,
        values,
    ):
        self.values = tuple(
            values
        )

    def all(
        self,
    ):
        return list(
            self.values
        )


class _ExecuteResult:
    def __init__(
        self,
        values,
    ):
        self.values = values

    def scalars(
        self,
    ):
        return _ScalarResult(
            self.values
        )


class _Session:
    def __init__(
        self,
        history,
    ):
        self.history = tuple(
            history
        )
        self.added = []
        self.flush_count = 0
        ids = [
            row.id
            for row in self.history
            if row.id is not None
        ]
        self.next_id = (
            max(
                ids,
                default=0,
            )
            + 1
        )

    async def execute(
        self,
        statement,
    ):
        return _ExecuteResult(
            self.history
        )

    def add(
        self,
        row,
    ):
        if row.id is None:
            row.id = self.next_id
            self.next_id += 1

        self.added.append(
            row
        )

    async def flush(
        self,
    ):
        self.flush_count += 1


@pytest.mark.asyncio
async def test_executor_changed_target_creates_reversal_then_replacement():
    original = event(
        1
    )

    db = _Session(
        (
            original,
        )
    )

    created = (
        await reconcile_purchase_return_vat_adjustment_source(
            db,
            company_id=1,
            target=target(
                adjustment_date=D2,
                base="60.00",
                tax="12.00",
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

    assert reversal.reversal_of_id == 1
    assert reversal.adjustment_date == D2
    assert (
        reversal.adjusted_taxable_base
        == Decimal("100.00")
    )
    assert (
        reversal.adjusted_tax_amount
        == Decimal("20.00")
    )

    assert replacement.reversal_of_id is None
    assert replacement.adjustment_date == D2
    assert (
        replacement.adjusted_taxable_base
        == Decimal("60.00")
    )
    assert (
        replacement.adjusted_tax_amount
        == Decimal("12.00")
    )

    assert original.reversal_of_id is None
    assert (
        original.adjusted_taxable_base
        == Decimal("100.00")
    )
    assert (
        original.adjusted_tax_amount
        == Decimal("20.00")
    )

    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_executor_zero_target_creates_reversal_only():
    original = event(
        1
    )

    db = _Session(
        (
            original,
        )
    )

    created = (
        await reconcile_purchase_return_vat_adjustment_source(
            db,
            company_id=1,
            target=target(
                base="0.00",
                tax="0.00",
            ),
            created_by=9,
            reversal_date=D2,
        )
    )

    assert len(
        created
    ) == 1

    assert created[
        0
    ].reversal_of_id == 1

    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_executor_exact_target_is_idempotent():
    db = _Session(
        (
            event(
                1
            ),
        )
    )

    created = (
        await reconcile_purchase_return_vat_adjustment_source(
            db,
            company_id=1,
            target=target(),
            created_by=9,
        )
    )

    assert created == ()
    assert db.added == []
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_reversal_date_cannot_precede_original():
    db = _Session(
        (
            event(
                1,
                adjustment_date=D2,
            ),
        )
    )

    with pytest.raises(
        PurchaseReturnVatAdjustmentPersistenceDataIntegrityError
    ):
        await reconcile_purchase_return_vat_adjustment_source(
            db,
            company_id=1,
            target=target(
                adjustment_date=D2,
                base="60.00",
                tax="12.00",
            ),
            created_by=9,
            reversal_date=D1,
        )


def test_service_contains_no_commit_or_rollback():
    source = inspect.getsource(
        reconcile_purchase_return_vat_adjustment_source
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
