from pathlib import Path

from app.models.payment import Payment


def test_cash_document_does_not_post_gl_directly():
    source = Path(
        "app/models/cash_document.py"
    ).read_text()

    assert "JournalEntry(" not in source
    assert "post_journal_entry" not in source


def test_payment_source_exclusivity_constraint_exists():
    names = {
        constraint.name
        for constraint in Payment.__table__.constraints
    }

    assert "ck_payments_single_money_source" in names


def test_no_cash_source_mutator_outside_payment_lifecycle():
    violations = []

    for path in Path("app/services").glob("*.py"):
        if path.name == "payment_lifecycle_service.py":
            continue

        source = path.read_text()

        if "payment.cash_desk_id =" in source:
            violations.append(path.name)

    assert violations == []


def test_cash_document_service_has_no_mutable_update_surface():
    service = Path(
        "app/services/cash_document_service.py"
    )

    assert service.exists()

    source = service.read_text()

    forbidden_mutators = (
        "update_cash_document",
        ".update(",
        "update(CashDocument",
        "original.document_number =",
        "original.direction =",
        "original.document_date =",
        "original.amount =",
        "original.currency_code =",
        "original.counterparty_id =",
        "original.contract_id =",
        "original.cash_desk_id =",
        "original.payment_id =",
        "original.reversal_of_id =",
    )

    for mutator in forbidden_mutators:
        assert mutator not in source


def test_cash_document_service_has_no_update_or_delete():
    from pathlib import Path

    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert ".delete(" not in source
    assert "delete(CashDocument" not in source

    forbidden_assignments = (
        "original.document_number =",
        "original.amount =",
        "original.currency_code =",
        "original.cash_desk_id =",
        "original.payment_id =",
        "original.reversal_of_id =",
    )

    for assignment in forbidden_assignments:
        assert assignment not in source


def test_cash_reversal_preserves_original_identity():
    from pathlib import Path

    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert "cash_desk_id=original.cash_desk_id" in source
    assert "payment_id=original.payment_id" in source
    assert "reversal_of_id=original.id" in source


def test_cash_reentry_is_new_original():
    from pathlib import Path

    source = Path(
        "app/services/cash_document_service.py"
    ).read_text()

    assert "create_cash_document_evidence(" in source
