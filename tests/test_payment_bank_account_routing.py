from pathlib import Path

from sqlalchemy import ForeignKeyConstraint

from app.models.payment import Payment


def test_payment_bank_account_source_is_nullable():
    column = (
        Payment.__table__.columns[
            "bank_account_id"
        ]
    )

    assert column.nullable is True


def test_payment_bank_account_fk_is_company_scoped():
    targets = {
        tuple(
            element.target_fullname
            for element
            in constraint.elements
        )
        for constraint
        in Payment.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "bank_accounts.company_id",
        "bank_accounts.id",
    ) in targets


def test_confirmation_journal_supports_concrete_bank_account():
    source = Path(
        "app/services/"
        "payment_journal_service.py"
    ).read_text()

    assert "BankAccount" in source
    assert "payment.bank_account_id" in source
    assert "bank_accounting_account_id" in source
    assert "accounting_account_id" in source


def test_semantic_bank_role_is_preserved():
    source = Path(
        "app/services/"
        "payment_accounting_service.py"
    ).read_text()

    assert "BANK_CURRENT_UAH" in source


def test_settlement_accounting_is_not_bank_source_routed():
    source = Path(
        "app/services/"
        "payment_journal_service.py"
    ).read_text()

    start = source.index(
        "async def generate_and_post_settlement_journal_entry"
    )

    section = source[start:]

    # Settlement continues using role-based AR/AP/advance
    # accounting. The BankAccount source is only relevant to
    # the original Payment confirmation.
    assert (
        "bank_accounting_account_id="
        not in section.split(
            "async def get_original_payment_journal_entry",
            1,
        )[0]
    )
