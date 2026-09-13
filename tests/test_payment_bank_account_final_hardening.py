from pathlib import Path


MIGRATION = Path(
    "alembic/versions/"
    "f6c8d0e2b135_add_payment_bank_account.py"
)

LIFECYCLE = Path(
    "app/services/payment_lifecycle_service.py"
)

JOURNAL = Path(
    "app/services/payment_journal_service.py"
)


def test_f6_downgrade_refuses_bank_source_provenance_loss():
    source = MIGRATION.read_text()

    downgrade = source[
        source.index("def downgrade() -> None:") :
    ]

    assert "op.get_bind()" in downgrade
    assert "bank_account_id IS NOT NULL" in downgrade
    assert "raise RuntimeError(" in downgrade

    assert "destroy historical " in downgrade
    assert "bank source provenance." in downgrade


def test_confirmation_requires_active_company_scoped_bank_account():
    source = LIFECYCLE.read_text()

    assert "payment.bank_account_id is not None" in source
    assert (
        "BankAccount.id"
        in source
    )
    assert (
        "BankAccount.company_id"
        in source
    )
    assert (
        "BankAccount.is_active.is_(True)"
        in source
    )


def test_confirmation_requires_matching_bank_currency():
    source = LIFECYCLE.read_text()

    assert (
        "Payment currency does not match "
        in source
    )
    assert (
        "BankAccount currency"
        in source
    )


def test_concrete_bank_gl_must_be_active_and_postable():
    source = JOURNAL.read_text()

    assert "Account.is_active.is_(True)" in source
    assert "Account.is_postable.is_(True)" in source

    assert (
        "inactive, non-postable"
        in source
    )


def test_payment_reversal_does_not_reresolve_bank_account():
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

    assert "BankAccount" not in section
    assert "bank_account_id" not in section
    assert "accounting_account_id" not in section


def test_no_payment_bank_source_mutator_service_exists():
    roots = [
        Path("app/services"),
        Path("app/api"),
    ]

    hits = []

    for root in roots:
        for path in root.rglob("*.py"):
            source = path.read_text()

            if "payment.bank_account_id =" in source:
                hits.append(str(path))

    assert hits == []
