from pathlib import Path


API = Path(
    "app/api/v1/cash_documents.py"
)
MAIN = Path("app/main.py")


def test_api_uses_payments_manage():
    source = API.read_text()

    assert '"payments.manage"' in source


def test_api_owns_transaction():
    source = API.read_text()

    assert "await db.commit()" in source
    assert "await db.rollback()" in source


def test_create_requires_idempotency_key():
    source = API.read_text()

    assert "Idempotency-Key" in source
    assert "idempotency_key" in source


def test_routes_exist():
    source = API.read_text()

    assert 'prefix="/companies/{company_id}/cash-documents"' in source
    assert '"/{cash_document_id}/reverse"' in source
    assert '"/{cash_document_id}/reentry"' in source


def test_router_registered():
    source = MAIN.read_text()

    assert "cash_documents_router" in source
    assert "app.include_router(" in source


def test_api_does_not_construct_payment_or_gl():
    source = API.read_text()

    assert "Payment(" not in source
    assert "JournalEntry(" not in source
    assert "JournalEntryLine(" not in source


def test_idempotency_payload_conflict_maps_to_409():
    source = API.read_text()

    assert (
        "CashDocumentPaymentIdempotencyConflictError"
        in source
    )

    marker = (
        "CashDocumentPaymentIdempotencyConflictError,"
    )
    assert marker in source

    assert (
        "status.HTTP_409_CONFLICT"
        in source
    )
