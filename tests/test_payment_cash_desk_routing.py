from pathlib import Path

from sqlalchemy import ForeignKeyConstraint

from app.models.payment import Payment


JOURNAL = Path(
    "app/services/payment_journal_service.py"
)


def test_payment_cash_desk_source_is_nullable():
    column = Payment.__table__.columns[
        "cash_desk_id"
    ]

    assert column.nullable is True


def test_payment_cash_desk_fk_is_company_scoped():
    targets = {
        tuple(
            element.target_fullname
            for element in constraint.elements
        )
        for constraint
        in Payment.__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "cash_desks.company_id",
        "cash_desks.id",
    ) in targets


def test_confirmation_journal_supports_concrete_cash_desk():
    source = JOURNAL.read_text()

    assert "CashDesk" in source
    assert "payment.cash_desk_id" in source
    assert "cash_accounting_account_id" in source
    assert "cash_desk.accounting_account_id" in source


def test_cash_source_replaces_semantic_money_leg_only():
    source = JOURNAL.read_text()

    assert (
        "AccountingAccountRole.BANK_CURRENT_UAH"
        in source
    )

    assert (
        "and cash_accounting_account is not None"
        in source
    )

    assert (
        "account = cash_accounting_account"
        in source
    )


def test_concrete_cash_gl_must_be_active_and_postable():
    source = JOURNAL.read_text()

    assert "Account.is_active.is_(True)" in source
    assert "Account.is_postable.is_(True)" in source

    assert (
        "CashDesk accounting account is missing, "
        in source
    )

    assert (
        "inactive, non-postable, or belongs to "
        in source
    )


def test_cash_confirmation_requires_active_company_cash_desk():
    source = JOURNAL.read_text()

    assert "CashDesk.id" in source
    assert "CashDesk.company_id" in source
    assert "CashDesk.is_active.is_(True)" in source


def test_cash_confirmation_requires_matching_currency():
    source = JOURNAL.read_text()

    assert (
        "Payment currency does not match "
        in source
    )
    assert "CashDesk currency" in source


def test_bank_and_cash_sources_fail_closed_in_journal():
    source = JOURNAL.read_text()

    assert "payment.bank_account_id is not None" in source
    assert "payment.cash_desk_id is not None" in source

    assert (
        "Payment cannot have both BankAccount "
        in source
    )
    assert "and CashDesk source" in source


def test_settlement_accounting_is_not_cash_source_routed():
    source = JOURNAL.read_text()

    start = source.index(
        "async def generate_and_post_settlement_journal_entry"
    )

    end = source.index(
        "async def get_original_payment_journal_entry"
    )

    section = source[start:end]

    assert "CashDesk" not in section
    assert "payment.cash_desk_id" not in section
    assert "cash_accounting_account_id=" not in section


def test_payment_reversal_does_not_reresolve_cash_desk():
    source = JOURNAL.read_text()

    start = source.index(
        "async def reverse_payment_journal_entry("
    )

    end = source.index(
        "async def reverse_settlement_journal_entry("
    )

    section = source[start:end]

    assert (
        "get_original_payment_journal_entry("
        in section
    )
    assert "reverse_journal_entry(" in section

    assert "CashDesk" not in section
    assert "cash_desk_id" not in section
    assert "accounting_account_id" not in section


def test_no_cash_desk_provenance_added_to_journal_entry():
    source = Path(
        "app/models/journal_entry.py"
    ).read_text()

    assert "cash_desk_id" not in source
