from pathlib import Path


SERVICE = Path(
    "app/services/"
    "cash_document_payment_orchestration_service.py"
)


def test_create_uses_sanctioned_payment_lifecycle():
    source = SERVICE.read_text()

    assert "create_payment_draft(" in source
    assert "confirm_payment(" in source
    assert "create_cash_document_evidence(" in source

    assert "Payment(" not in source
    assert "JournalEntry(" not in source
    assert "JournalEntryLine(" not in source


def test_create_derives_payment_from_cash_facts():
    source = SERVICE.read_text()

    assert "cash_desk_id=cash_desk_id" in source
    assert "payment_date=document_date" in source
    assert "currency_code=currency_code" in source
    assert "amount=amount" in source
    assert "direction=direction" in source


def test_create_is_idempotent():
    source = SERVICE.read_text()

    assert "reserve_idempotent_request(" in source
    assert "generate_request_fingerprint(" in source
    assert "complete_idempotent_operation(" in source
    assert "IdempotencyDecision.REUSE_RESULT" in source

    assert (
        'CASH_DOCUMENT_PAYMENT_CREATE_OPERATION = ('
        in source
    )
    assert (
        'CASH_DOCUMENT_PAYMENT_RESULT_TYPE = "cash_document"'
        in source
    )


def test_replay_uses_same_cash_document_result():
    source = SERVICE.read_text()

    start = source.index(
        "if execution.decision == IdempotencyDecision.REUSE_RESULT:"
    )
    end = source.index(
        "if (\n        execution.decision"
    )

    replay = source[start:end]

    assert "int(result.result_id)" in replay
    assert "get_cash_document(" in replay
    assert "_get_payment_for_document(" in replay

    assert "create_payment_draft(" not in replay
    assert "confirm_payment(" not in replay


def test_reverse_cancels_existing_payment_then_appends_evidence():
    source = SERVICE.read_text()

    start = source.index(
        "async def cancel_and_reverse_cash_payment("
    )
    end = source.index(
        "async def create_cash_payment_reentry("
    )

    section = source[start:end]

    assert "cancel_payment(" in section
    assert "reverse_cash_document_evidence(" in section

    assert "create_payment_draft(" not in section
    assert "confirm_payment(" not in section
    assert "Payment(" not in section


def test_reentry_creates_new_payment_and_new_original():
    source = SERVICE.read_text()

    start = source.index(
        "async def create_cash_payment_reentry("
    )

    section = source[start:]

    assert "create_payment_draft(" in section
    assert "confirm_payment(" in section

    assert (
        "create_cash_document_reentry_evidence("
        in section
    )


def test_orchestration_owns_no_transaction():
    source = SERVICE.read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_orchestration_does_not_settle_payment():
    source = SERVICE.read_text()

    assert "payment_settlement" not in source
    assert (
        "create_payment_settlement_allocation"
        not in source
    )


def test_idempotency_key_reuse_is_wrapped_by_orchestration():
    source = SERVICE.read_text()

    assert (
        "from app.services.idempotency_request_validator import ("
        in source
    )
    assert "IdempotencyKeyReuseError" in source

    assert (
        "class CashDocumentPaymentIdempotencyConflictError("
        in source
    )

    assert (
        "except IdempotencyKeyReuseError as exc:"
        in source
    )

    assert (
        "raise CashDocumentPaymentIdempotencyConflictError("
        in source
    )


def test_double_reversal_guard_precedes_payment_cancellation():
    source = SERVICE.read_text()

    start = source.index(
        "async def cancel_and_reverse_cash_payment("
    )
    end = source.index(
        "async def create_cash_payment_reentry("
    )

    section = source[start:end]

    guard = section.index(
        "CashDocumentAlreadyReversedError("
    )
    cancellation = section.index(
        "cancel_payment("
    )

    assert guard < cancellation

    assert (
        "CashDocument.reversal_of_id == original.id"
        in section
    )
