from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.bank_account import BankAccount
from app.schemas.bank_account import (
    BankAccountCreate,
)


def test_bank_account_table_contract():
    table = BankAccount.__table__

    assert table.name == "bank_accounts"

    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "company_id",
        "name",
        "account_number",
        "currency_code",
        "accounting_account_id",
        "is_active",
        "created_at",
        "updated_at",
    }


def test_bank_account_company_identity_constraints():
    table = BankAccount.__table__

    unique_sets = {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "company_id",
        "id",
    ) in unique_sets

    assert (
        "company_id",
        "account_number",
    ) in unique_sets


def test_bank_account_has_company_safe_account_fk():
    table = BankAccount.__table__

    composite_fks = []

    for constraint in table.constraints:
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        local = tuple(
            element.parent.name
            for element in constraint.elements
        )

        remote = tuple(
            element.target_fullname
            for element in constraint.elements
        )

        composite_fks.append(
            (local, remote)
        )

    assert (
        (
            "company_id",
            "accounting_account_id",
        ),
        (
            "accounts.company_id",
            "accounts.id",
        ),
    ) in composite_fks


def test_bank_account_check_constraints():
    names = {
        constraint.name
        for constraint
        in BankAccount.__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        "ck_bank_accounts_name_nonempty"
        in names
    )
    assert (
        "ck_bank_accounts_number_nonempty"
        in names
    )
    assert (
        "ck_bank_accounts_currency_code_length"
        in names
    )


def test_bank_account_create_normalizes_values():
    payload = BankAccountCreate(
        name="  Main UAH account  ",
        account_number="  ua123abc  ",
        currency_code="uah",
        accounting_account_id=10,
    )

    assert payload.name == "Main UAH account"
    assert payload.account_number == "UA123ABC"
    assert payload.currency_code == "UAH"
