from datetime import date
from decimal import Decimal
from pathlib import Path
import ast

import pytest

import app.services.purchase_value_correction_fifo_impact_persistence_service as service

from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
)
from app.services.purchase_value_correction_fifo_impact_calculation_service import (
    PurchaseValueCorrectionFifoImpactTarget,
)
from app.services.purchase_value_correction_fifo_impact_persistence_service import (
    PurchaseValueCorrectionFifoImpactChronologyError,
    PurchaseValueCorrectionFifoImpactDataIntegrityError,
    reconcile_purchase_value_correction_fifo_impact_source,
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

D3 = date(
    2026,
    9,
    3,
)


class FakeDb:
    def __init__(
        self,
    ):
        self.added = []
        self.flush_count = 0

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

        if getattr(
            value,
            "id",
            None,
        ) is None:
            value.id = (
                1000
                + len(
                    self.added
                )
            )

    async def flush(
        self,
    ):
        self.flush_count += 1


def target(
    *,
    original="10.00",
    corrected="8.00",
    quantity="10",
    recognition_date=D1,
    destination_kind="on_hand",
    consumption_id=None,
    issue_document_id=None,
    issue_line_id=None,
):
    return (
        PurchaseValueCorrectionFifoImpactTarget(
            purchase_value_correction_allocation_event_id=10,
            invoice_fulfillment_allocation_id=20,
            stock_lot_id=30,
            destination_kind=destination_kind,
            stock_lot_consumption_id=consumption_id,
            issue_document_id=issue_document_id,
            issue_document_line_id=issue_line_id,
            quantity=Decimal(
                quantity
            ),
            recognition_date=recognition_date,
            original_base_amount=Decimal(
                original
            ),
            corrected_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
        )
    )


def event(
    *,
    event_id=1,
    original="10.00",
    corrected="8.00",
    quantity="10",
    recognition_date=D1,
    destination_kind="on_hand",
    consumption_id=None,
    issue_document_id=None,
    issue_line_id=None,
    reversal_of_id=None,
):
    value = (
        PurchaseValueCorrectionFifoImpactEvent(
            company_id=1,
            purchase_value_correction_allocation_event_id=10,
            stock_lot_id=30,
            destination_kind=destination_kind,
            stock_lot_consumption_id=consumption_id,
            issue_document_id=issue_document_id,
            issue_document_line_id=issue_line_id,
            recognition_date=recognition_date,
            quantity=Decimal(
                quantity
            ),
            original_base_amount=Decimal(
                original
            ),
            corrected_base_amount=Decimal(
                corrected
            ),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=reversal_of_id,
        )
    )

    value.id = event_id

    return value


@pytest.fixture
def stub_sources(
    monkeypatch,
):
    calls = []

    async def validate(
        db,
        *,
        company_id,
        target,
    ):
        calls.append(
            (
                company_id,
                target,
            )
        )

    monkeypatch.setattr(
        service,
        "_validate_positive_target_sources",
        validate,
    )

    return calls


def stub_history(
    monkeypatch,
    values,
):
    async def load(
        db,
        *,
        company_id,
        allocation_event_id,
    ):
        return tuple(
            values
        )

    monkeypatch.setattr(
        service,
        "_load_history_for_update",
        load,
    )


def test_target_ifa_id_must_be_positive():
    bad = target()

    object.__setattr__(
        bad,
        "invoice_fulfillment_allocation_id",
        0,
    )

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactDataIntegrityError,
        match="invoice_fulfillment_allocation_id",
    ):
        service._validate_target_shape(
            bad
        )


@pytest.mark.asyncio
async def test_new_noop_target_creates_nothing(
    monkeypatch,
    stub_sources,
):
    stub_history(
        monkeypatch,
        (),
    )

    db = FakeDb()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                corrected="10.00",
            ),
            created_by=2,
        )
    )

    assert result == ()
    assert db.added == []
    assert stub_sources == []


@pytest.mark.asyncio
async def test_new_positive_target_creates_original(
    monkeypatch,
    stub_sources,
):
    stub_history(
        monkeypatch,
        (),
    )

    db = FakeDb()

    desired = target()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=desired,
            created_by=2,
        )
    )

    assert len(
        result
    ) == 1

    created = result[0]

    assert created.reversal_of_id is None
    assert created.recognition_date == D1
    assert (
        created.original_base_amount
        == Decimal("10.00")
    )
    assert (
        created.corrected_base_amount
        == Decimal("8.00")
    )
    assert len(
        stub_sources
    ) == 1


@pytest.mark.asyncio
async def test_exact_active_target_is_idempotent(
    monkeypatch,
    stub_sources,
):
    current = event()

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(),
            created_by=2,
        )
    )

    assert result == ()
    assert db.added == []
    assert stub_sources == []


@pytest.mark.asyncio
async def test_active_to_noop_requires_explicit_reversal_date(
    monkeypatch,
    stub_sources,
):
    stub_history(
        monkeypatch,
        (
            event(),
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactChronologyError,
        match="explicit reversal_date",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                corrected="10.00",
            ),
            created_by=2,
        )


@pytest.mark.asyncio
async def test_active_to_noop_creates_reversal_only(
    monkeypatch,
    stub_sources,
):
    current = event()

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                corrected="10.00",
            ),
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(
        result
    ) == 1

    reversal = result[0]

    assert reversal.reversal_of_id == current.id
    assert reversal.recognition_date == D2
    assert (
        reversal.original_base_amount
        == current.original_base_amount
    )
    assert (
        reversal.corrected_base_amount
        == current.corrected_base_amount
    )
    assert stub_sources == []


@pytest.mark.asyncio
async def test_changed_amount_creates_reversal_and_forward_replacement(
    monkeypatch,
    stub_sources,
):
    current = event()

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    desired = target(
        corrected="7.00",
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=desired,
            created_by=2,
            reversal_date=D3,
        )
    )

    assert len(
        result
    ) == 2

    reversal, replacement = result

    assert reversal.reversal_of_id == current.id
    assert reversal.recognition_date == D3

    assert replacement.reversal_of_id is None
    assert replacement.recognition_date == D3
    assert (
        replacement.corrected_base_amount
        == Decimal("7.00")
    )

    assert len(
        stub_sources
    ) == 1


@pytest.mark.asyncio
async def test_changed_quantity_creates_replacement(
    monkeypatch,
    stub_sources,
):
    current = event()

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                quantity="8",
                original="8.00",
                corrected="6.00",
            ),
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(
        result
    ) == 2
    assert (
        result[1].quantity
        == Decimal("8")
    )


@pytest.mark.asyncio
async def test_date_only_difference_is_change_at_persistence_layer(
    monkeypatch,
    stub_sources,
):
    current = event(
        recognition_date=D1,
    )

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                recognition_date=D2,
            ),
            created_by=2,
            reversal_date=D2,
        )
    )

    assert len(
        result
    ) == 2
    assert result[0].recognition_date == D2
    assert result[1].recognition_date == D2


@pytest.mark.asyncio
async def test_reversal_date_cannot_predate_current(
    monkeypatch,
    stub_sources,
):
    current = event(
        recognition_date=D2,
    )

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactChronologyError,
        match="cannot predate",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                corrected="7.00",
            ),
            created_by=2,
            reversal_date=D1,
        )


@pytest.mark.asyncio
async def test_forward_replacement_cannot_predate_primary_target_date(
    monkeypatch,
    stub_sources,
):
    current = event(
        recognition_date=D1,
    )

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactChronologyError,
        match="primary recognition_date",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(
                corrected="7.00",
                recognition_date=D3,
            ),
            created_by=2,
            reversal_date=D2,
        )


@pytest.mark.asyncio
async def test_multiple_active_same_key_fails_closed(
    monkeypatch,
    stub_sources,
):
    stub_history(
        monkeypatch,
        (
            event(
                event_id=1,
            ),
            event(
                event_id=2,
            ),
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactDataIntegrityError,
        match="Multiple active",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(),
            created_by=2,
        )


@pytest.mark.asyncio
async def test_reversal_must_preserve_snapshot(
    monkeypatch,
    stub_sources,
):
    original = event(
        event_id=1,
    )

    bad_reversal = event(
        event_id=2,
        corrected="7.00",
        recognition_date=D2,
        reversal_of_id=1,
    )

    stub_history(
        monkeypatch,
        (
            original,
            bad_reversal,
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactDataIntegrityError,
        match="immutable economic snapshot",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(),
            created_by=2,
        )


@pytest.mark.asyncio
async def test_reversal_cannot_reverse_reversal(
    monkeypatch,
    stub_sources,
):
    original = event(
        event_id=1,
    )

    reversal = event(
        event_id=2,
        recognition_date=D2,
        reversal_of_id=1,
    )

    reversal_of_reversal = event(
        event_id=3,
        recognition_date=D3,
        reversal_of_id=2,
    )

    stub_history(
        monkeypatch,
        (
            original,
            reversal,
            reversal_of_reversal,
        ),
    )

    db = FakeDb()

    with pytest.raises(
        PurchaseValueCorrectionFifoImpactDataIntegrityError,
        match="cannot reverse another reversal",
    ):
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=target(),
            created_by=2,
        )


@pytest.mark.asyncio
async def test_issued_destination_identity_is_part_of_key(
    monkeypatch,
    stub_sources,
):
    current = event(
        destination_kind="issued",
        consumption_id=101,
        issue_document_id=201,
        issue_line_id=301,
    )

    stub_history(
        monkeypatch,
        (
            current,
        ),
    )

    db = FakeDb()

    desired = target(
        destination_kind="issued",
        consumption_id=102,
        issue_document_id=202,
        issue_line_id=302,
    )

    result = (
        await reconcile_purchase_value_correction_fifo_impact_source(
            db,
            company_id=1,
            target=desired,
            created_by=2,
        )
    )

    assert len(
        result
    ) == 1
    assert result[0].stock_lot_consumption_id == 102


def test_service_has_no_commit_or_rollback():
    path = Path(
        "app/services/"
        "purchase_value_correction_fifo_"
        "impact_persistence_service.py"
    )

    tree = ast.parse(
        path.read_text()
    )

    for node in ast.walk(
        tree
    ):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if not isinstance(
            node.func,
            ast.Attribute,
        ):
            continue

        assert node.func.attr not in {
            "commit",
            "rollback",
        }
