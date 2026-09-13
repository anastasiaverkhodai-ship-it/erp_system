from pathlib import Path


JOURNAL = Path(
    "app/services/payment_journal_service.py"
)
LIFECYCLE = Path(
    "app/services/payment_lifecycle_service.py"
)


def test_payment_reversal_keeps_original_cash_gl_mapping():
    source = JOURNAL.read_text()

    start = source.index(
        "async def reverse_payment_journal_entry("
    )

    end = source.index(
        "async def reverse_settlement_journal_entry("
    )

    section = source[start:end]

    assert "get_original_payment_journal_entry(" in section
    assert "reverse_journal_entry(" in section

    assert "CashDesk" not in section
    assert "cash_desk_id" not in section


def test_cash_source_is_checked_before_confirmation():
    source = LIFECYCLE.read_text()

    assert "payment.cash_desk_id is not None" in source
    assert "CashDesk.id == payment.cash_desk_id" in source
    assert "CashDesk.company_id == payment.company_id" in source
    assert "CashDesk.is_active.is_(True)" in source


def test_cash_source_currency_is_checked_before_confirmation():
    source = LIFECYCLE.read_text()

    assert (
        "Payment currency does not match CashDesk currency"
        in source
    )


def test_payment_journal_keeps_legacy_null_source_support():
    source = JOURNAL.read_text()

    # Neither concrete source is required.
    # Legacy NULL/NULL Payment continues resolving
    # BANK_CURRENT_UAH through account roles.
    assert (
        "bank_accounting_account_id: int | None = None"
        in source
    )
    assert (
        "cash_accounting_account_id: int | None = None"
        in source
    )


def test_cash_source_does_not_change_settlement_accounting():
    source = JOURNAL.read_text()

    start = source.index(
        "async def generate_and_post_settlement_journal_entry"
    )

    end = source.index(
        "async def get_original_payment_journal_entry"
    )

    section = source[start:end]

    assert "cash_accounting_account_id" not in section
    assert "CashDesk" not in section
