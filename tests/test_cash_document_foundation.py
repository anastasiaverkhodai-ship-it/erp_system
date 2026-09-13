from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.cash_document import CashDocument
from app.models.payment import Payment


def _foreign_keys(table):
    return {
        (
            tuple(
                element.parent.name
                for element in constraint.elements
            ),
            tuple(
                element.target_fullname
                for element in constraint.elements
            ),
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }


def test_payment_has_cash_desk_source_identity():
    assert "cash_desk_id" in Payment.__table__.c

    assert (
        ("company_id", "cash_desk_id"),
        ("cash_desks.company_id", "cash_desks.id"),
    ) in _foreign_keys(Payment.__table__)


def test_payment_forbids_bank_and_cash_source_together():
    names = {
        constraint.name
        for constraint in Payment.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert "ck_payments_single_money_source" in names


def test_cash_document_table_contract():
    assert {
        column.name
        for column in CashDocument.__table__.columns
    } == {
        "id",
        "company_id",
        "cash_desk_id",
        "payment_id",
        "document_number",
        "direction",
        "document_date",
        "amount",
        "currency_code",
        "counterparty_id",
        "contract_id",
        "external_reference",
        "description",
        "created_by",
        "created_at",
        "reversal_of_id",
    }


def test_cash_document_company_scoped_identity():
    unique_sets = {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint
        in CashDocument.__table__.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert ("company_id", "id") in unique_sets

    assert (
        "company_id",
        "direction",
        "document_number",
    ) in unique_sets

    assert (
        "company_id",
        "id",
        "cash_desk_id",
        "payment_id",
    ) in unique_sets


def test_cash_document_cash_desk_fk():
    assert (
        ("company_id", "cash_desk_id"),
        ("cash_desks.company_id", "cash_desks.id"),
    ) in _foreign_keys(CashDocument.__table__)


def test_cash_document_payment_provenance_fk():
    assert (
        ("company_id", "payment_id"),
        ("payments.company_id", "payments.id"),
    ) in _foreign_keys(CashDocument.__table__)


def test_cash_document_reversal_identity_fk():
    assert (
        (
            "company_id",
            "reversal_of_id",
            "cash_desk_id",
            "payment_id",
        ),
        (
            "cash_documents.company_id",
            "cash_documents.id",
            "cash_documents.cash_desk_id",
            "cash_documents.payment_id",
        ),
    ) in _foreign_keys(CashDocument.__table__)


def test_cash_document_is_immutable_event_shape():
    columns = {
        column.name
        for column in CashDocument.__table__.columns
    }

    assert "status" not in columns
    assert "updated_at" not in columns
