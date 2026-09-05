import asyncio
import inspect
from datetime import date
from decimal import Decimal

import pytest

import app.services.purchase_return_recognition_persistence_service as service
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.services.purchase_return_recognition_calculation_service import (
    PurchaseReturnRecognitionTarget,
)
from app.services.purchase_return_recognition_persistence_service import (
    PurchaseReturnRecognitionPersistenceDataIntegrityError,
    reconcile_purchase_return_recognition_source,
)


class _ScalarResult:
    def __init__(
        self,
        rows,
    ):
        self.rows = list(
            rows
        )

    def scalars(
        self,
    ):
        return self

    def all(
        self,
    ):
        return list(
            self.rows
        )


class _FakeDB:
    def __init__(
        self,
        history=(),
    ):
        self.history = tuple(
            history
        )
        self.added = []
        self.flush_count = 0
        self.statement = None
        self.next_id = 100

    async def execute(
        self,
        statement,
    ):
        self.statement = statement

        return _ScalarResult(
            self.history
        )

    def add(
        self,
        event,
    ):
        if getattr(
            event,
            "id",
            None,
        ) is None:
            event.id = self.next_id
            self.next_id += 1

        self.added.append(
            event
        )

    async def flush(
        self,
    ):
        self.flush_count += 1


def _target(
    *,
    quantity: str = "1",
    base: str = "10.00",
    gross: str = "12.00",
    tax: str = "2.00",
    day: int = 10,
):
    return PurchaseReturnRecognitionTarget(
        return_source_id=50,
        economic_source_id=70,
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


def _event(
    event_id: int,
    *,
    quantity: str = "1",
    base: str = "10.00",
    gross: str = "12.00",
    tax: str = "2.00",
    day: int = 10,
    reversal_of_id=None,
):
    return PurchaseReturnRecognitionEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=50,
        invoice_fulfillment_allocation_id=70,
        recognition_date=date(
            2026,
            1,
            day,
        ),
        returned_quantity=Decimal(
            quantity
        ),
        returned_base_amount=Decimal(
            base
        ),
        returned_gross_amount=Decimal(
            gross
        ),
        returned_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        created_by=9,
        reversal_of_id=(
            reversal_of_id
        ),
    )


def _reconcile(
    db,
    *,
    target,
    reversal_date=None,
):
    return asyncio.run(
        reconcile_purchase_return_recognition_source(
            db,
            company_id=1,
            trade_return_event_id=50,
            invoice_fulfillment_allocation_id=70,
            created_by=9,
            target=target,
            reversal_date=reversal_date,
        )
    )


def test_creates_original_for_new_pair():
    db = _FakeDB()

    result = _reconcile(
        db,
        target=_target(),
    )

    assert len(
        db.added
    ) == 1

    event = db.added[0]

    assert event.reversal_of_id is None

    assert (
        event.returned_base_amount
        == Decimal("10.00")
    )

    assert (
        result.active_event
        is event
    )

    assert db.flush_count == 1


def test_identical_active_target_is_noop():
    original = _event(
        1
    )

    db = _FakeDB(
        (
            original,
        )
    )

    result = _reconcile(
        db,
        target=_target(),
    )

    assert db.added == []
    assert db.flush_count == 0

    assert (
        result.active_event
        is original
    )


def test_removal_creates_immutable_reversal():
    original = _event(
        1,
        base="0.02",
        gross="0.02",
        tax="0.01",
    )

    db = _FakeDB(
        (
            original,
        )
    )

    result = _reconcile(
        db,
        target=None,
        reversal_date=date(
            2026,
            1,
            20,
        ),
    )

    assert len(
        db.added
    ) == 1

    reversal = db.added[0]

    assert (
        reversal.reversal_of_id
        == original.id
    )

    assert (
        reversal.recognition_date
        == date(
            2026,
            1,
            20,
        )
    )

    assert (
        reversal.returned_quantity
        == original.returned_quantity
    )

    assert (
        reversal.returned_base_amount
        == original.returned_base_amount
    )

    assert (
        reversal.returned_gross_amount
        == original.returned_gross_amount
    )

    assert (
        reversal.returned_tax_amount
        == original.returned_tax_amount
    )

    assert (
        reversal.currency_code
        == original.currency_code
    )

    assert result.active_event is None


def test_changed_state_creates_reversal_and_full_replacement():
    original = _event(
        1,
        quantity="2",
        base="20.00",
        gross="24.00",
        tax="4.00",
    )

    db = _FakeDB(
        (
            original,
        )
    )

    replacement_target = _target(
        quantity="1",
        base="10.00",
        gross="12.00",
        tax="2.00",
        day=15,
    )

    result = _reconcile(
        db,
        target=replacement_target,
        reversal_date=date(
            2026,
            1,
            20,
        ),
    )

    assert len(
        db.added
    ) == 2

    reversal = db.added[0]
    replacement = db.added[1]

    assert (
        reversal.reversal_of_id
        == original.id
    )

    assert replacement.reversal_of_id is None

    assert (
        replacement.recognition_date
        == date(
            2026,
            1,
            15,
        )
    )

    assert (
        replacement.returned_quantity
        == Decimal("1")
    )

    assert (
        replacement.returned_base_amount
        == Decimal("10.00")
    )

    assert (
        replacement.returned_gross_amount
        == Decimal("12.00")
    )

    assert (
        replacement.returned_tax_amount
        == Decimal("2.00")
    )

    assert (
        result.active_event
        is replacement
    )


def test_positive_quantity_zero_base_is_persisted():
    db = _FakeDB()

    result = _reconcile(
        db,
        target=_target(
            quantity="1",
            base="0.00",
            gross="0.01",
            tax="0.00",
        ),
    )

    assert len(
        result.created_events
    ) == 1

    assert (
        result.created_events[0]
        .returned_quantity
        == Decimal("1")
    )

    assert (
        result.created_events[0]
        .returned_base_amount
        == Decimal("0.00")
    )


def test_reversal_requires_explicit_business_date():
    db = _FakeDB(
        (
            _event(
                1
            ),
        )
    )

    with pytest.raises(
        PurchaseReturnRecognitionPersistenceDataIntegrityError,
        match="reversal_date is required",
    ):
        _reconcile(
            db,
            target=None,
        )


def test_more_than_one_active_original_is_rejected():
    db = _FakeDB(
        (
            _event(
                1
            ),
            _event(
                2
            ),
        )
    )

    with pytest.raises(
        PurchaseReturnRecognitionPersistenceDataIntegrityError,
        match="more than one active original",
    ):
        _reconcile(
            db,
            target=_target(),
        )


def test_pair_history_is_locked_for_update():
    source = inspect.getsource(
        service._load_pair_history
    )

    assert (
        ".with_for_update()"
        in source
    )


def test_service_never_commits_or_rolls_back():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
