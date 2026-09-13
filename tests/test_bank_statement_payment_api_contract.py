from pathlib import Path

from app.main import app
from app.schemas.bank_statement_payment import (
    BankStatementCreatePaymentRequest,
    BankStatementExistingPaymentMatchRequest,
)


API = Path(
    "app/api/v1/bank_statements.py"
)


def test_bank_statement_payment_routes_registered():
    paths = app.openapi()["paths"]

    existing = (
        "/api/v1/companies/{company_id}"
        "/bank-statements/lines/"
        "{bank_statement_line_id}/reconcile-existing"
    )

    create = (
        "/api/v1/companies/{company_id}"
        "/bank-statements/lines/"
        "{bank_statement_line_id}/create-payment"
    )

    assert existing in paths
    assert create in paths

    assert "post" in paths[existing]
    assert "post" in paths[create]


def test_bank_mutation_api_reuses_payments_manage():
    source = API.read_text()

    assert source.count(
        '"payments.manage"'
    ) >= 2

    assert "bank_statements.manage" not in source


def test_create_from_line_request_cannot_override_bank_facts():
    fields = set(
        BankStatementCreatePaymentRequest
        .model_fields.keys()
    )

    assert fields == {
        "counterparty_id",
        "contract_id",
        "number",
        "external_reference",
        "description",
    }

    forbidden = {
        "bank_account_id",
        "direction",
        "payment_date",
        "currency_code",
        "amount",
        "matched_amount",
    }

    assert fields.isdisjoint(forbidden)


def test_existing_match_request_is_narrow():
    fields = set(
        BankStatementExistingPaymentMatchRequest
        .model_fields.keys()
    )

    assert fields == {
        "payment_id",
        "matched_amount",
    }


def test_api_owns_transaction():
    source = API.read_text()

    assert "await db.commit()" in source
    assert "await db.rollback()" in source


def test_api_does_not_construct_payment_or_gl():
    source = API.read_text()

    assert "Payment(" not in source
    assert "JournalEntry(" not in source
    assert "JournalEntryLine(" not in source


def test_api_calls_sanctioned_orchestration_only():
    source = API.read_text()

    assert (
        "reconcile_bank_statement_line_to_payment("
        in source
    )
    assert (
        "create_confirm_and_reconcile_bank_statement_line("
        in source
    )

    assert "create_payment_draft(" not in source
    assert "confirm_payment(" not in source
