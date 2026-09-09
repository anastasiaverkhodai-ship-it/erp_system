from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)

from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
)


def test_table_name():
    assert (
        PurchaseValueCorrectionFifoImpactEvent
        .__tablename__
        == "purchase_value_correction_fifo_impact_events"
    )


def test_exact_columns():
    assert set(
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.c.keys()
    ) == {
        "id",
        "company_id",
        "purchase_value_correction_allocation_event_id",
        "stock_lot_id",
        "destination_kind",
        "stock_lot_consumption_id",
        "issue_document_id",
        "issue_document_line_id",
        "recognition_date",
        "quantity",
        "original_base_amount",
        "corrected_base_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    }


def test_numeric_shapes():
    quantity = (
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.c.quantity.type
    )

    original = (
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.c.original_base_amount.type
    )

    corrected = (
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.c.corrected_base_amount.type
    )

    assert isinstance(
        quantity,
        Numeric,
    )

    assert quantity.precision == 18
    assert quantity.scale == 4

    assert original.precision == 18
    assert original.scale == 2

    assert corrected.precision == 18
    assert corrected.scale == 2


def test_required_check_constraints():
    names = {
        constraint.name
        for constraint in (
            PurchaseValueCorrectionFifoImpactEvent
            .__table__.constraints
        )
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        "ck_pvcfi_event_destination_kind",
        "ck_pvcfi_event_quantity_positive",
        "ck_pvcfi_event_original_nonnegative",
        "ck_pvcfi_event_corrected_nonnegative",
        "ck_pvcfi_event_not_noop",
        "ck_pvcfi_event_currency_length",
        "ck_pvcfi_event_not_self_reversal",
        "ck_pvcfi_event_destination_provenance",
    } <= names


def test_required_unique_constraints():
    names = {
        constraint.name
        for constraint in (
            PurchaseValueCorrectionFifoImpactEvent
            .__table__.constraints
        )
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_pvcfi_event_company_id_id"
        in names
    )

    assert (
        "uq_pvcfi_event_reversal_of"
        in names
    )


def test_required_foreign_keys():
    targets = set()

    for constraint in (
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.constraints
    ):
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        targets.add(
            tuple(
                element.target_fullname
                for element in constraint.elements
            )
        )

    assert (
        (
            "companies.id",
        )
        in targets
    )

    assert (
        (
            "purchase_value_correction_allocation_events.company_id",
            "purchase_value_correction_allocation_events.id",
        )
        in targets
    )

    assert (
        (
            "stock_lots.id",
        )
        in targets
    )

    assert (
        (
            "stock_lot_consumptions.id",
        )
        in targets
    )

    assert (
        (
            "documents.id",
        )
        in targets
    )

    assert (
        (
            "document_lines.id",
        )
        in targets
    )

    assert (
        (
            "users.id",
        )
        in targets
    )

    assert (
        (
            "purchase_value_correction_fifo_impact_events.id",
        )
        in targets
    )


def test_required_indexes():
    names = {
        index.name
        for index in (
            PurchaseValueCorrectionFifoImpactEvent
            .__table__.indexes
        )
        if isinstance(
            index,
            Index,
        )
    }

    assert {
        "ix_pvcfi_event_allocation",
        "ix_pvcfi_event_stock_lot",
        "ix_pvcfi_event_consumption",
        "ix_pvcfi_event_recognition_date",
    } <= names


def test_base_amount_delta():
    event = (
        PurchaseValueCorrectionFifoImpactEvent(
            company_id=1,
            purchase_value_correction_allocation_event_id=1,
            stock_lot_id=1,
            destination_kind="on_hand",
            stock_lot_consumption_id=None,
            issue_document_id=None,
            issue_document_line_id=None,
            recognition_date=date(
                2026,
                9,
                1,
            ),
            quantity=Decimal("1.0000"),
            original_base_amount=Decimal("0.02"),
            corrected_base_amount=Decimal("0.01"),
            currency_code="UAH",
            created_by=1,
            reversal_of_id=None,
        )
    )

    assert (
        event.base_amount_delta
        == Decimal("-0.01")
    )


def test_no_journal_supplier_or_vat_columns():
    columns = set(
        PurchaseValueCorrectionFifoImpactEvent
        .__table__.c.keys()
    )

    assert "journal_entry_id" not in columns
    assert (
        "supplier_advance_clearing_event_id"
        not in columns
    )
    assert "tax_recognition_event_id" not in columns
