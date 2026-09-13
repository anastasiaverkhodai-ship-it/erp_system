from pathlib import Path


ORCHESTRATION = Path(
    "app/services/"
    "bank_statement_payment_orchestration_service.py"
)

RECONCILIATION = Path(
    "app/services/"
    "bank_statement_reconciliation_service.py"
)

STATEMENT_SERVICE = Path(
    "app/services/bank_statement_service.py"
)


def test_single_line_reader_exists():
    source = STATEMENT_SERVICE.read_text()

    assert "async def get_bank_statement_line(" in source
    assert "company_id == company_id" in source
    assert "BankStatementLine.id" in source
    assert "lock_for_update" in source


def test_reconciliation_has_concrete_bank_identity_guard():
    source = RECONCILIATION.read_text()

    assert (
        "BankStatementReconciliationBankAccountError"
        in source
    )
    assert "payment.bank_account_id is not None" in source
    assert (
        "payment.bank_account_id"
        in source
    )
    assert "line.bank_account_id" in source


def test_orchestration_uses_only_payment_lifecycle():
    source = ORCHESTRATION.read_text()

    assert "create_payment_draft(" in source
    assert "confirm_payment(" in source

    assert "Payment(" not in source
    assert "JournalEntry(" not in source
    assert "JournalEntryLine(" not in source


def test_create_from_line_derives_bank_facts():
    source = ORCHESTRATION.read_text()

    assert "direction=_direction_from_line(line)" in source
    assert "payment_date=line.transaction_date" in source
    assert "currency_code=line.currency_code" in source
    assert "amount=_full_line_amount(line)" in source
    assert "bank_account_id=line.bank_account_id" in source


def test_create_from_line_reconciles_full_line_amount():
    source = ORCHESTRATION.read_text()

    assert (
        "matched_amount=_full_line_amount(line)"
        in source
    )


def test_existing_payment_path_does_not_create_payment():
    source = ORCHESTRATION.read_text()

    start = source.index(
        "async def reconcile_bank_statement_line_to_payment("
    )
    end = source.index(
        "async def create_confirm_and_reconcile_bank_statement_line("
    )

    existing_path = source[start:end]

    assert "create_payment_draft(" not in existing_path
    assert "confirm_payment(" not in existing_path
    assert (
        "create_bank_statement_reconciliation("
        in existing_path
    )


def test_orchestration_service_does_not_own_transaction():
    source = ORCHESTRATION.read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_orchestration_does_not_settle_payment():
    source = ORCHESTRATION.read_text()

    assert "payment_settlement" not in source
    assert "create_payment_settlement_allocation" not in source
